import asyncio
import copy
import random
import time
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from books import integrity
from optimizer import walk, payoff, allowed, D


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class PaperEngine:
    def __init__(self, feed, db):
        self.feed, self.db = feed, db

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

    async def execute(self, session, req):
        async with session['lock']:
            prior = await self.db.trades.find_one({'session_id': session['id'], 'request_id': req.request_id}, {'_id': 0, 'session_id': 0})
            if prior:
                return prior
            quote = session['quotes'].get(req.quote_id)
            if not quote or quote.get('kind') == 'manual' or time.time() - quote['created'] > 10:
                raise HTTPException(409, 'Recommendation expired. Wait for a fresh opportunity.')
            settings = session['settings'].model_copy(deep=True)
            if quote['settings'] != settings.model_dump() or settings.paused:
                raise HTTPException(409, 'Strategy changed. Wait for a new recommendation.')
            market = self.feed.snapshot(settings.asset)
            if not market or quote['market_id'] != market.get('id'):
                raise HTTPException(409, 'Market rolled over; the old recommendation is invalid.')
            record = {'id': str(uuid.uuid4()), 'session_id': session['id'], 'request_id': req.request_id,
                      'created_at': utcnow(), 'asset': settings.asset, 'market_id': market['id'],
                      'market_slug': market['slug'], 'market_end': market['end'], 'mode': 'PAPER',
                      'scenario': req.scenario, 'objective': settings.objective, 'settings': settings.model_dump(),
                      'requested': copy.deepcopy(quote['decision']), 'actual': None, 'fills': {},
                      'remaining_shares': {'up': 0, 'down': 0}, 'sale_proceeds': 0, 'events': [],
                      'actual_debit': 0, 'realized_pnl': None, 'order_type': 'FOK per leg', 'atomic': False}
            self.event(record, 'EXECUTING', 'Revalidating independent UP and DOWN paper orders')
            await self.save(session, record)
            await asyncio.sleep(settings.latency_ms / 1000)
            market = self.feed.snapshot(settings.asset)
            state, reason = integrity(market, settings)
            if state != 'LIVE' or market.get('id') != record['market_id'] or req.scenario in ('stale', 'unsynced', 'failed'):
                self.event(record, 'ABORTED', f'Preflight rejected: {req.scenario if req.scenario != "normal" else reason}')
                await self.save(session, record)
                return self.public(record)
            legs = [self.fill(market, side, quote['decision'][side], settings, 1) for side in ('up', 'down')]
            if not all(legs) or not allowed(payoff(*legs, settings.budget), settings, session['cash']):
                self.event(record, 'ABORTED', 'Latest depth, price, balance or payoff no longer satisfies the approved trade')
                await self.save(session, record)
                return self.public(record)
            async def submit(side):
                await asyncio.sleep(random.uniform(0.01, 0.06))
                latest = self.feed.snapshot(settings.asset)
                state, _ = integrity(latest, settings)
                if state != 'LIVE' or latest['id'] != record['market_id']:
                    return side, None
                if (req.scenario == 'one_sided' and side == 'down') or random.random() < settings.failure_rate:
                    return side, None
                retention = 0.05 if req.scenario == 'liquidity' else settings.liquidity_retention
                return side, self.fill(latest, side, quote['decision'][side], settings, retention)
            results = await asyncio.gather(submit('up'), submit('down'))
            for side, filled in results:
                if filled:
                    record['fills'][side] = filled
                    record['remaining_shares'][side] = filled['shares']
            record['actual_debit'] = float(sum((D(x['total_debit']) for x in record['fills'].values()), D(0)))
            session['cash'] = float(D(session['cash']) - D(record['actual_debit']))
            if len(record['fills']) == 2:
                record['actual'] = payoff(record['fills']['up'], record['fills']['down'], settings.budget)
                self.event(record, 'LOCKED_POSITION', 'Both FOK paper legs filled; actual fees and payoff reconciled')
            elif not record['fills']:
                self.event(record, 'ABORTED', 'Both legs rejected; no paper capital spent')
            else:
                self.event(record, 'RECOVERY_REQUIRED', 'One leg filled. Position is exposed; evaluating retry versus unwind.')
                await self.save(session, record)
                await self.recover(session, record, settings, allow_retry=req.scenario != 'one_sided')
            await self.save(session, record)
            await self.feed.notify()
            return self.public(record)

    async def recover(self, session, record, settings, allow_retry=True):
        market = self.feed.snapshot(record['asset'])
        state, reason = integrity(market, settings)
        if state != 'LIVE' or market['id'] != record['market_id']:
            self.event(record, 'RECOVERY_REQUIRED', f'Recovery locked: {reason}. Exposure retained, not assumed closed.')
            return
        missing = next((side for side in ('up', 'down') if side not in record['fills']), None)
        if missing and allow_retry:
            filled = self.fill(market, missing, record['requested'][missing], settings, settings.liquidity_retention)
            if filled and filled['total_debit'] <= session['cash']:
                combined = {**record['fills'], missing: filled}
                result = payoff(combined['up'], combined['down'], settings.budget)
                if allowed(result, settings, session['cash'] + record['actual_debit']) and result['worst'] >= -settings.max_recovery_loss:
                    record['fills'] = combined
                    record['remaining_shares'][missing] = filled['shares']
                    record['actual_debit'] = result['deployed']
                    record['actual'] = result
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