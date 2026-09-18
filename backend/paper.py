import asyncio
import copy
import random
import time
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from books import integrity
from execution_math import walk, payoff, D, value
from strict_arbitrage import optimize, validate_pair
from strict_policy import STRATEGY, POLICY_VERSION
from quote_store import QuoteStore, market_binding


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class PaperEngine:
    def __init__(self, feed, db):
        self.feed, self.db = feed, db
        self.quotes = QuoteStore(db)

    async def save(self, session, record):
        await self.db.trades.update_one({'id': record['id'], 'session_id': session['id']}, {'$set': copy.deepcopy(record)}, upsert=True)
        await self.db.sessions.update_one({'id': session['id']}, {'$set': {'cash': session['cash']}})

    def event(self, record, state, message):
        record['status'] = state
        record['events'].append({'state': state, 'at': utcnow(), 'message': message})

    def fill(self, market, side, leg, settings, retention):
        b = market['books'][side]
        quantity = D(leg['shares'])
        if quantity < D(b['minimum']) or quantity % (D(10) ** -b['size_precision']):
            return None
        # FOK per leg; there is intentionally no claim of pair atomicity.
        ceiling = leg['highest_ask']
        levels = [(p, float(D(q) * D(retention))) for p, q in b['asks'] if p <= ceiling + 1e-10]
        result = walk(levels, leg['shares'], market['fee'], b['size_precision'])
        if not result or result['total_debit'] > leg['total_debit'] + 0.000001:
            return None
        return result

    def strict_fill(self, market, side, q, envelope, settings, retention):
        book = market['books'][side]
        quantity = D(q)
        if quantity < D(book['minimum']) or quantity % (D(10)**-book['size_precision']):
            return None
        limits = envelope[side]
        levels = [(D(p),D(size)*D(retention)) for p,size in book['asks'] if D(p)<=D(limits['limit_price'])]
        result = walk(levels,quantity,market['fee'],book['size_precision'])
        if not result or value(result,'total_debit')>D(limits['max_debit']):
            return None
        if not book['asks'] or value(result,'highest_ask')>D(book['asks'][0][0])*(1+D(settings.max_slippage_pct)/100):
            return None
        return result

    async def execute(self, session, req):
        async with session['lock']:
            prior = await self.db.trades.find_one({'session_id': session['id'], 'request_id': req.request_id}, {'_id': 0, 'session_id': 0})
            if prior:
                if prior.get('quote_id') != req.quote_id or prior.get('trade_type') != STRATEGY:
                    raise HTTPException(409, 'Request identifier is bound to another trade.')
                return prior
            quote = await self.quotes.load(session, req.quote_id, STRATEGY)
            settings = session['settings'].model_copy(deep=True)
            if settings.paused or quote.get('policy_version') != POLICY_VERSION:
                raise HTTPException(409, 'Strategy changed. Wait for a new recommendation.')
            market = self.feed.snapshot(settings.asset)
            initial_state, initial_reason = integrity(market,settings)
            if initial_state != 'LIVE':
                raise HTTPException(409,initial_reason)
            if quote['binding'] != market_binding(market):
                raise HTTPException(409, 'Market, token or fee configuration changed. Request a fresh quote.')
            q, envelope = quote['decision']['q'], quote['decision']['envelope']
            record = {'id': str(uuid.uuid4()), 'session_id': session['id'], 'request_id': req.request_id,
                      'quote_id':req.quote_id, 'trade_type':STRATEGY, 'policy_version':POLICY_VERSION,
                      'q':q, 'envelope':copy.deepcopy(envelope),
                      'created_at': utcnow(), 'asset': settings.asset, 'market_id': market['id'],
                      'market_slug': market['slug'], 'market_end': market['end'], 'mode': 'PAPER',
                      'scenario': req.scenario, 'objective': STRATEGY, 'settings': settings.model_dump(),
                      'requested': copy.deepcopy(quote['decision']), 'actual': None, 'fills': {},
                      'remaining_shares': {'up': 0, 'down': 0}, 'sale_proceeds': 0, 'events': [],
                      'actual_debit': 0, 'realized_pnl': None, 'order_type': 'FOK per leg', 'atomic': False}
            await self.quotes.claim(session, quote, req.request_id, record['id'])
            self.event(record, 'EXECUTING', 'Revalidating the smallest equal-Q strict paper pair and economic envelope')
            await self.save(session, record)
            await asyncio.sleep(settings.latency_ms / 1000)
            market = self.feed.snapshot(settings.asset)
            state, reason = integrity(market, settings)
            if state != 'LIVE' or quote['expires_at']<=time.time() or market_binding(market)!=quote['binding'] or req.scenario in ('stale', 'unsynced', 'failed'):
                self.event(record, 'ABORTED', f'Preflight rejected: {req.scenario if req.scenario != "normal" else reason}')
                await self.save(session, record)
                await self.quotes.finish(session,req.quote_id,record['status'])
                return self.public(record)
            latest_decision = await asyncio.to_thread(optimize, market, settings, session['cash'])
            legs = [self.strict_fill(market, side, q, envelope, settings, 1) for side in ('up', 'down')]
            valid,_ = validate_pair(*legs,settings,session['cash'],envelope,q)
            if latest_decision['status']!='READY' or D(latest_decision.get('q',0))!=D(q) or not valid:
                self.event(record, 'ABORTED', 'Minimum Q, current economics or execution envelope changed. A fresh quote is required.')
                await self.save(session, record)
                await self.quotes.finish(session,req.quote_id,record['status'])
                return self.public(record)
            async def submit(side):
                await asyncio.sleep(random.uniform(0.01, 0.06))
                latest = self.feed.snapshot(settings.asset)
                state, _ = integrity(latest, settings)
                if state != 'LIVE' or quote['expires_at']<=time.time() or market_binding(latest)!=quote['binding']:
                    return side, None
                if (req.scenario == 'one_sided' and side == 'down') or random.random() < settings.failure_rate:
                    return side, None
                retention = 0.05 if req.scenario == 'liquidity' else settings.liquidity_retention
                return side, self.strict_fill(latest, side, q, envelope, settings, retention)
            results = await asyncio.gather(submit('up'), submit('down'))
            for side, filled in results:
                if filled:
                    record['fills'][side] = filled
                    record['remaining_shares'][side] = filled['shares']
            debit_exact = sum((value(x,'total_debit') for x in record['fills'].values()), D(0))
            cash_before = D(session['cash'])
            record['actual_debit'], record['actual_debit_exact'] = float(debit_exact), str(debit_exact)
            session['cash'] = float(cash_before-debit_exact)
            if len(record['fills']) == 2:
                record['actual'] = payoff(record['fills']['up'], record['fills']['down'], settings.budget)
                qualified, reason = validate_pair(record['fills']['up'],record['fills']['down'],settings,cash_before,envelope,q)
                record['strict_qualified'] = qualified
                self.event(record, 'LOCKED_POSITION' if qualified else 'RECONCILIATION_EXCEPTION',
                           'Equal Q filled; actual combined cost and $0.12 profit floor verified.' if qualified else f'Actual fills do not qualify as strict arbitrage: {reason}. No top-up order submitted.')
            elif not record['fills']:
                self.event(record, 'ABORTED', 'Both legs rejected; no paper capital spent')
            else:
                self.event(record, 'RECOVERY_REQUIRED', 'One leg filled. Position is exposed; evaluating retry versus unwind.')
                await self.save(session, record)
                await self.recover(session, record, settings, allow_retry=req.scenario != 'one_sided')
            await self.save(session, record)
            await self.quotes.finish(session,req.quote_id,record['status'])
            await self.feed.notify()
            return self.public(record)

    async def recover(self, session, record, settings, allow_retry=True):
        market = self.feed.snapshot(record['asset'])
        state, reason = integrity(market, settings)
        if state != 'LIVE' or market['id'] != record['market_id']:
            self.event(record, 'RECOVERY_REQUIRED', f'Recovery locked: {reason}. Exposure retained, not assumed closed.')
            return
        missing = next((side for side in ('up', 'down') if side not in record['fills']), None)
        if missing and allow_retry and record.get('trade_type') == STRATEGY and record.get('policy_version') == POLICY_VERSION:
            original = await self.db.quotes.find_one({'quote_id':record['quote_id'], 'session_id':session['id']}, {'_id':0})
            compatible = original and original['binding']==market_binding(market)
            filled = self.strict_fill(market,missing,record['q'],record['envelope'],settings,settings.liquidity_retention) if compatible else None
            if filled and filled['total_debit'] <= session['cash']:
                combined = {**record['fills'], missing: filled}
                result = payoff(combined['up'], combined['down'], settings.budget)
                valid,_ = validate_pair(combined['up'],combined['down'],settings,session['cash']+record['actual_debit'],record['envelope'],record['q'])
                if valid:
                    record['fills'] = combined
                    record['remaining_shares'][missing] = filled['shares']
                    record['actual_debit'] = result['deployed']
                    record['actual_debit_exact'] = result['exact']['deployed']
                    record['actual'] = result
                    record['strict_qualified'] = True
                    session['cash'] = float(D(session['cash']) - D(filled['total_debit']))
                    self.event(record, 'LOCKED_POSITION', f'Recovery: retried {missing.upper()} within objective and recovery loss limits')
                    return
        exits = {}
        for side, quantity in record['remaining_shares'].items():
            if quantity:
                b = market['books'][side]
                sale = walk(b['bids'], quantity, market['fee'], b['size_precision'], selling=True)
                if not sale:
                    self.event(record, 'RECOVERY_REQUIRED', 'Insufficient bid depth for a complete unwind')
                    return
                exits[side] = sale
        proceeds = sum((D(x['net_proceeds']) for x in exits.values()), D(0))
        loss = D(record['actual_debit']) - proceeds
        if loss > D(settings.max_recovery_loss):
            self.event(record, 'RECOVERY_REQUIRED', 'Unwind exceeds maximum recovery loss; exposure remains open')
            return
        record['unwind_fills'] = exits
        record['sale_proceeds'] = float(proceeds)
        record['remaining_shares'] = {'up': 0, 'down': 0}
        record['realized_pnl'] = float(proceeds - D(record['actual_debit']))
        session['cash'] = float(D(session['cash']) + proceeds)
        self.event(record, 'UNWOUND', 'Recovery: sold actual exposure against bid depth, including sell fees')

    @staticmethod
    def public(record):
        return {k: v for k, v in record.items() if k not in ('_id', 'session_id')}