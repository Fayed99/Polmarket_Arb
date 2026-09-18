import asyncio
import copy
import hashlib
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).parent / '.env')
from models import Settings, ExecutionRequest, ManualRequest, StoredRecord, ManualBuyQuoteRequest, ManualBuyExecuteRequest
from feed import MarketFeed
from books import integrity, side_integrity
from optimizer import optimize, no_trade, walk, payoff, D
from paper import PaperEngine, utcnow
from manual import ManualPaperEngine
from spot import SpotFeed

logging.basicConfig(level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)
client = AsyncIOMotorClient(os.environ['MONGO_URL'])
db = client[os.environ['DB_NAME']]
feed = MarketFeed()
engine = PaperEngine(feed, db)
manual_engine = ManualPaperEngine(engine)
spot = SpotFeed(feed.registry, feed.notify)
sessions = {}
session_lock = asyncio.Lock()


async def get_session(sid):
    try:
        UUID(sid)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, 'Valid session identifier required')
    async with session_lock:
        if sid not in sessions:
            doc = await db.sessions.find_one({'id': sid}, {'_id': 0})
            if not doc:
                doc = {'id': sid, 'settings': Settings().model_dump(), 'cash': float(os.environ['PAPER_INITIAL_BALANCE']), 'created_at': utcnow()}
                await db.sessions.insert_one(copy.deepcopy(doc))
            sessions[sid] = {'id': sid, 'settings': Settings(**doc['settings']), 'cash': doc['cash'], 'quotes': {}, 'lock': asyncio.Lock()}
        return sessions[sid]


async def settlement_loop():
    while not feed.stopping:
        try:
            records = await db.trades.find({'status': {'$in': ['LOCKED_POSITION', 'OPEN_POSITION', 'RECOVERY_REQUIRED']}, 'market_end': {'$lt': time.time() - 10}}, {'_id': 0}).to_list(100)
            market_cache = {}
            for record in records:
                slug = record['market_slug']
                if slug not in market_cache:
                    market_cache[slug] = await feed.get(f'{feed.gamma}/markets/slug/{slug}')
                raw = market_cache[slug]
                prices = json.loads(raw.get('outcomePrices', '[]')) if isinstance(raw.get('outcomePrices'), str) else raw.get('outcomePrices', [])
                outcomes = json.loads(raw.get('outcomes', '[]')) if isinstance(raw.get('outcomes'), str) else raw.get('outcomes', [])
                if not raw.get('closed') or raw.get('umaResolutionStatus') != 'resolved' or len(prices) != 2 or sorted(float(p) for p in prices) != [0, 1]:
                    continue
                winner = outcomes[next(i for i, p in enumerate(prices) if float(p) == 1)].lower()
                session = await get_session(record['session_id'])
                async with session['lock']:
                    current = await db.trades.find_one({'id': record['id']}, {'_id': 0})
                    if not current or current['status'] not in ('LOCKED_POSITION', 'OPEN_POSITION', 'RECOVERY_REQUIRED'):
                        continue
                    record = current
                    payout = record['remaining_shares'].get(winner, 0)
                    session['cash'] = float(D(session['cash']) + D(payout))
                    record['winner'], record['payout'] = winner, payout
                    record['realized_pnl'] = float(D(payout) + D(record['sale_proceeds']) - D(record['actual_debit']))
                    record['remaining_shares'] = {'up': 0, 'down': 0}
                    engine.event(record, 'SETTLED', f'Exchange confirmed {winner.upper()} resolution; paper payout credited')
                    await engine.save(session, record)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception('Settlement check failed')
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app):
    await db.sessions.create_index('id', unique=True)
    await db.trades.create_index('id', unique=True)
    await db.trades.create_index([('session_id', 1), ('request_id', 1)], unique=True)
    await db.trades.update_many({'status': 'EXECUTING'}, {'$set': {'status': 'RECOVERY_REQUIRED'}, '$push': {'events': {'state': 'RECOVERY_REQUIRED', 'at': utcnow(), 'message': 'Application restarted during paper execution; reconciliation required'}}})
    await feed.start()
    await spot.start()
    task = asyncio.create_task(settlement_loop())
    yield
    feed.stopping = True
    task.cancel()
    await asyncio.wait([task], timeout=3)
    await feed.stop()
    await spot.stop()
    client.close()


app = FastAPI(title='Parity · Opportunity Optimizer', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.environ['CORS_ORIGINS'].split(','), allow_credentials=False, allow_methods=['GET', 'PUT', 'POST'], allow_headers=['Content-Type', 'X-Session-ID'])


async def state(session):
    settings = session['settings'].model_copy(deep=True)
    market = feed.snapshot(settings.asset)
    decision = await asyncio.to_thread(optimize, market, settings, session['cash'])
    # Recheck age after optimization; never publish a now-expired recommendation.
    status, reason = integrity(market, settings)
    if decision['status'] == 'READY' and status != 'LIVE':
        decision = no_trade(settings, status, reason)
    if decision['status'] == 'READY':
        key = hashlib.sha256(json.dumps([market['id'], decision['up']['shares'], decision['down']['shares'], decision['deployed'], settings.model_dump()], sort_keys=True).encode()).hexdigest()[:24]
        decision['quote_id'] = key
        session['quotes'] = {k: v for k, v in session['quotes'].items() if time.time() - v['created'] <= 10}
        session['quotes'][key] = {'created': time.time(), 'decision': copy.deepcopy(decision), 'settings': settings.model_dump(), 'market_id': market['id']}
    markets = []
    for entry in feed.registry:
        m = feed.snapshot(entry['asset'])
        s, _ = integrity(m, settings)
        books = (m or {}).get('books', {})
        markets.append({**entry, 'status': s, 'id': (m or {}).get('id'), 'image': (m or {}).get('image'),
                        'end': (m or {}).get('end'), 'up_ask': books.get('up', {}).get('asks', [[None]])[0][0] if books.get('up', {}).get('asks') else None,
                        'down_ask': books.get('down', {}).get('asks', [[None]])[0][0] if books.get('down', {}).get('asks') else None})
    books = (market or {}).get('books', {})
    now = time.time() * 1000
    diagnostics = {'status': status, 'reason': reason, 'sync_ms': abs(books['up']['event_ms'] - books['down']['event_ms']) if len(books) == 2 else None,
                   'events': feed.events, 'resyncs': feed.resyncs, 'uptime': time.time() - feed.started,
                   'book_ages': {s: round(now-b['arrival_ms']) for s,b in books.items()},
                   'event_ages': {s: round(now-b['event_ms']) for s,b in books.items()}, 'version': feed.version,
                   'reconnects': feed.reconnects, 'timestamp_regressions': feed.timestamp_regressions}
    manual_quotes = {}
    for side in ('up', 'down'):
        side_status, side_reason = side_integrity(market, side, settings)
        manual_quotes[side] = {'status': side_status, 'reason': side_reason}
    return {'settings': settings.model_dump(), 'markets': markets, 'market': market, 'decision': decision,
            'diagnostics': diagnostics, 'wallet': {'cash': session['cash'], 'mode': 'PAPER'}, 'server_time': time.time(), 'live_execution': False,
            'reference': spot.snapshot(settings.asset), 'manual_quotes': manual_quotes}


@app.get('/api/health')
async def health():
    return {'status': 'ok', 'mode': 'PAPER', 'live_execution': False, 'events': feed.events, 'markets': {a: m.get('connected', False) for a,m in feed.markets.items()},
            'binance': {asset: spot.snapshot(asset)['status'] for asset in spot.assets}}


@app.get('/api/state')
async def get_state(x_session_id: str = Header(...)):
    return await state(await get_session(x_session_id))


@app.get('/api/stream')
async def stream(request: Request, session_id: str = Query(...)):
    session = await get_session(session_id)
    async def events():
        opened = time.monotonic()
        yield 'retry: 300\n\n'
        # Bound stream lifetime so graceful updates never wait indefinitely on clients.
        while time.monotonic() - opened < 30 and not await request.is_disconnected():
            cycle_start = time.monotonic()
            changed = feed.changed
            try:
                data = await state(session)
                yield f'data: {json.dumps(data, allow_nan=False)}\n\n'
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception('State calculation failed')
                yield 'event: feed-error\ndata: {"message":"Calculation unavailable; execution disabled"}\n\n'
                await asyncio.sleep(1)
            try:
                await asyncio.wait_for(changed.wait(), max(0.001, 1-(time.monotonic()-cycle_start)))
            except asyncio.TimeoutError:
                pass
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.put('/api/settings', response_model=Settings)
async def save_settings(settings: Settings, x_session_id: str = Header(...)):
    session = await get_session(x_session_id)
    async with session['lock']:
        session['settings'] = settings
        session['quotes'].clear()
        await db.sessions.update_one({'id': session['id']}, {'$set': {'settings': settings.model_dump()}})
    await feed.notify()
    return settings


@app.post('/api/paper/execute', response_model=StoredRecord)
async def execute(req: ExecutionRequest, x_session_id: str = Header(...)):
    session = await get_session(x_session_id)
    return await asyncio.shield(engine.execute(session, req))


@app.post('/api/live/execute')
async def live_execute():
    raise HTTPException(423, 'Real trading is locked. This build only supports paper execution; no wallet secrets or signing path are configured.')


@app.get('/api/reference')
async def references():
    return {asset: spot.snapshot(asset) for asset in spot.assets}


@app.get('/api/readme')
async def readme():
    return FileResponse(Path(__file__).parent.parent / 'README.md', media_type='text/markdown', filename='README.md')


@app.post('/api/paper/manual/quote')
async def manual_quote(req: ManualBuyQuoteRequest, x_session_id: str = Header(...)):
    return await manual_engine.quote(await get_session(x_session_id), req)


@app.post('/api/paper/manual/execute', response_model=StoredRecord)
async def manual_execute(req: ManualBuyExecuteRequest, x_session_id: str = Header(...)):
    return await asyncio.shield(manual_engine.execute(await get_session(x_session_id), req))


@app.get('/api/trades', response_model=list[StoredRecord])
async def trades(x_session_id: str = Header(...)):
    session = await get_session(x_session_id)
    return await db.trades.find({'session_id': session['id']}, {'_id': 0, 'session_id': 0}).sort('created_at', -1).to_list(300)


@app.post('/api/trades/{trade_id}/recover', response_model=StoredRecord)
async def recover(trade_id: str, x_session_id: str = Header(...)):
    session = await get_session(x_session_id)
    async with session['lock']:
        record = await db.trades.find_one({'session_id': session['id'], 'id': trade_id}, {'_id': 0})
        if not record:
            raise HTTPException(404, 'Paper trade not found')
        if record['status'] != 'RECOVERY_REQUIRED':
            raise HTTPException(409, 'This position does not require recovery')
        await engine.recover(session, record, Settings(**record['settings']))
        await engine.save(session, record)
        return engine.public(record)


@app.post('/api/simulate')
async def simulate(req: ManualRequest, x_session_id: str = Header(...)):
    session = await get_session(x_session_id)
    market = feed.snapshot(session['settings'].asset)
    status, reason = integrity(market, session['settings'])
    if status != 'LIVE':
        raise HTTPException(409, reason)
    fills = []
    for side, q in zip(('up', 'down'), (req.up_shares, req.down_shares)):
        b = market['books'][side]
        step = D(10) ** -b['size_precision']
        if q < b['minimum'] or D(q) % step:
            raise HTTPException(422, f'{side.upper()} must be at least {b["minimum"]} shares, with {b["size_precision"]} decimal places')
        fill = walk(b['asks'], q, market['fee'], b['size_precision'])
        if not fill:
            raise HTTPException(409, 'Insufficient live liquidity')
        fills.append(fill)
    result = payoff(*fills, session['settings'].budget)
    return {**result, 'simulation_only': True, 'within_budget': result['deployed'] <= min(session['settings'].budget, session['cash'])}