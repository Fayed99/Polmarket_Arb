import asyncio
import copy
import json
import logging
import os
import time
from datetime import datetime
import httpx
import websockets
from books import Book

logger = logging.getLogger(__name__)


class MarketFeed:
    def __init__(self):
        self.registry = json.loads(os.environ['MARKET_REGISTRY'])
        self.gamma, self.clob, self.ws_url = (os.environ[k] for k in ('POLY_GAMMA_URL', 'POLY_CLOB_URL', 'POLY_WS_URL'))
        self.http = httpx.AsyncClient(timeout=12)
        self.markets, self.tasks = {}, []
        self.changed = asyncio.Event()
        self.version, self.events, self.resyncs = 0, 0, 0
        self.started = time.time()
        self.stopping = False
        self.reconnects = 0
        self.timestamp_regressions = 0

    async def notify(self):
        self.version += 1
        previous = self.changed
        self.changed = asyncio.Event()
        previous.set()

    async def get(self, url, **params):
        res = await self.http.get(url, params=params)
        res.raise_for_status()
        return res.json()

    async def start(self):
        self.tasks = [asyncio.create_task(self.run(entry)) for entry in self.registry]

    async def stop(self):
        self.stopping = True
        for task in self.tasks:
            task.cancel()
        await asyncio.wait(self.tasks, timeout=3)
        await self.http.aclose()

    def snapshot(self, asset):
        m = self.markets.get(asset)
        if not m:
            return None
        return {**{k: copy.deepcopy(v) for k, v in m.items() if k != 'books'},
                'books': {side: book.public() for side, book in m.get('books', {}).items()}}

    async def discover(self, entry):
        duration = entry['duration']
        epoch = int(time.time()) // duration * duration
        slug = f"{entry['prefix']}-{duration // 60}m-{epoch}"
        raw = await self.get(f'{self.gamma}/markets/slug/{slug}')
        if raw.get('closed') or not raw.get('active') or not raw.get('acceptingOrders'):
            raise ValueError('Current market is not accepting orders')
        end = datetime.fromisoformat(raw['endDate'].replace('Z', '+00:00')).timestamp()
        if not epoch <= time.time() < end or end != epoch + duration:
            raise ValueError('Market window mismatch')
        info = await self.get(f"{self.clob}/clob-markets/{raw['conditionId']}")
        fee = info.get('fd')
        if not fee or any(k not in fee for k in ('r', 'e', 'to')) or float(fee['e']) != 1 or not 0 <= float(fee['r']) <= 1:
            raise ValueError('Fee configuration cannot be safely verified')
        if info.get('ao') is not True:
            raise ValueError('CLOB orders are disabled')
        tokens = {t['o'].lower(): t['t'] for t in info['t']}
        if set(tokens) != {'up', 'down'}:
            raise ValueError('Ambiguous UP / DOWN token mapping')
        books = {side: Book(token, info['mts'], info['mos']) for side, token in tokens.items()}
        return {**entry, 'id': raw['id'], 'slug': slug, 'condition_id': raw['conditionId'],
                'question': raw['question'], 'start': epoch, 'end': end, 'fee': fee,
                'fee_formula': 'shares × rate × price × (1 − price); collateral fee, 5 decimal places',
                'image': raw.get('image'), 'connected': False, 'error': None,
                'books': books, 'config': raw.get('cryptoMarketConfig', {}), 'event_count': 0}

    async def resnapshot(self, market):
        snapshots = await asyncio.gather(*(self.get(f'{self.clob}/book', token_id=b.token) for b in market['books'].values()))
        for b, data in zip(market['books'].values(), snapshots):
            b.snapshot(data)
        self.resyncs += 1

    async def run(self, entry):
        backoff = 1
        market = None
        while not self.stopping:
            try:
                if not market or time.time() >= market['end'] or market.get('refresh_config', False):
                    fresh = await self.discover(entry)
                    market = fresh
                    self.markets[entry['asset']] = market
                await self.resnapshot(market)
                await self.notify()
                market['stream_started'] = time.time()
                await self.stream(market)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('%s feed: %s', entry['asset'], str(exc)[:240])
                if market:
                    market['connected'] = False
                    market['error'] = f'Feed reconnecting: {str(exc)[:140]}'
                    for b in market['books'].values():
                        b.ready = False
                else:
                    previous = self.markets.get(entry['asset'], {**entry, 'books': {}})
                    previous.update(error='Market discovery unavailable; retrying', connected=False)
                    self.markets[entry['asset']] = previous
                self.reconnects += 1
                await self.notify()
                if market and time.time() - market.get('stream_started', 0) > 5:
                    backoff = 0.25
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5)

    async def stream(self, market):
        lookup = {book.token: book for book in market['books'].values()}
        pending = set(lookup)
        # REST is recovery only. Require a fresh WS snapshot before permitting calculations.
        for book in lookup.values():
            book.ready = False
            book.initialized = False
            book.anomaly_since = None
        async with websockets.connect(self.ws_url, ping_interval=None, open_timeout=12, close_timeout=2) as ws:
            await ws.send(json.dumps({'assets_ids': list(lookup), 'type': 'market', 'initial_dump': True}))
            last_ping = time.time()
            last_message = time.time()
            while time.time() < market['end'] and not self.stopping:
                if time.time() - last_ping >= 10:
                    await ws.send('PING')
                    last_ping = time.time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    if time.time() - last_message > 20:
                        raise ValueError('WebSocket heartbeat timeout')
                    if any(b.anomaly_since and time.monotonic() - b.anomaly_since > 0.3 for b in lookup.values()):
                        raise ValueError('Persistent crossed book; fetching fresh snapshot')
                    continue
                last_message = time.time()
                if raw in ('PONG', 'pong'):
                    continue
                payload = json.loads(raw)
                changed = False
                touched = set()
                for msg in payload if isinstance(payload, list) else [payload]:
                    kind = msg.get('event_type')
                    if kind == 'book' and msg.get('asset_id') in lookup:
                        lookup[msg['asset_id']].snapshot(msg, defer_validation=True)
                        pending.discard(msg['asset_id'])
                        touched.add(msg['asset_id'])
                        changed = True
                    elif kind == 'price_change':
                        for item in msg.get('price_changes', []):
                            token = item['asset_id']
                            if token in lookup:
                                stamp = int(msg['timestamp'])
                                if stamp < lookup[token].snapshot_ms:
                                    # The full snapshot already supersedes this queued older event.
                                    lookup[token].timestamp_regressions += 1
                                    self.timestamp_regressions += 1
                                    continue
                                lookup[token].change(item, int(msg['timestamp']))
                                touched.add(token)
                        changed = bool(touched) or changed
                    elif kind in ('tick_size_change', 'market_resolved'):
                        market['refresh_config'] = True
                        raise ValueError('Market configuration changed; rebuilding books')
                # Validate only after the entire message/batch. A transient crossed state
                # disables recommendations immediately but gets 300 ms to normalize.
                for token in touched:
                    book = lookup[token]
                    try:
                        book.validate()
                        book.ready, book.anomaly_since = True, None
                    except ValueError:
                        book.ready = False
                        if book.anomaly_since is None:
                            book.anomaly_since = time.monotonic()
                        elif time.monotonic() - book.anomaly_since > 0.3:
                            raise ValueError('Persistent crossed book; fetching fresh snapshot')
                if changed:
                    market['connected'] = not pending
                    market['error'] = None
                    market['event_count'] += 1
                    self.events += 1
                    await self.notify()
                    await asyncio.sleep(0)
        market['connected'] = False
        await self.notify()