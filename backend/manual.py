"""Intentional one-sided PAPER orders; never treat these as failed auto hedges."""
import asyncio
import copy
import random
import time
import uuid
from decimal import ROUND_DOWN
from fastapi import HTTPException
from books import side_integrity
from optimizer import D, walk, payoff
from paper import utcnow


def capital_cap(settings, cash):
    return min(D(settings.budget), D(cash), D(settings.max_deployment) if settings.max_deployment is not None else D(settings.budget))


def quote_single(market, settings, cash, request):
    state, reason = side_integrity(market, request.side, settings)
    if state != 'LIVE':
        raise HTTPException(409, reason)
    book = market['books'][request.side]
    best = D(book['asks'][0][0])
    ceiling = best * (1 + D(settings.max_slippage_pct) / 100)
    levels = [(p,q) for p,q in book['asks'] if D(p) <= ceiling]
    step = D(10) ** -book['size_precision']
    cap = capital_cap(settings, cash)
    if request.unit == 'dollars':
        limit = D(request.amount)
        if limit > cap:
            raise HTTPException(422, f'Spend must be within the budget, deployment limit and paper cash ({float(cap):.2f} available).')
        lo, hi = 0, int((sum((D(q) for _,q in levels), D(0)) / step).to_integral_value(rounding=ROUND_DOWN))
        while lo < hi:
            middle = (lo + hi + 1) // 2
            fill = walk(levels, D(middle) * step, market['fee'], book['size_precision'])
            if fill and D(fill['total_debit']) <= limit:
                lo = middle
            else:
                hi = middle - 1
        quantity = D(lo) * step
    else:
        quantity = D(request.amount)
        if quantity % step:
            raise HTTPException(422, f'Share quantity supports {book["size_precision"]} decimal places.')
    if quantity < D(book['minimum']):
        minimum_fill = walk(levels, book['minimum'], market['fee'], book['size_precision'])
        hint = f' (about ${minimum_fill["total_debit"]:.4f} including fees)' if minimum_fill else ''
        raise HTTPException(422, f'Minimum order is {book["minimum"]:g} shares{hint}.')
    fill = walk(levels, quantity, market['fee'], book['size_precision'])
    if not fill:
        raise HTTPException(409, 'Not enough liquidity within your slippage limit.')
    if D(fill['total_debit']) > cap:
        raise HTTPException(422, 'This order exceeds your budget, deployment limit or available paper balance.')
    return fill


class ManualPaperEngine:
    def __init__(self, paper):
        self.paper, self.feed, self.db = paper, paper.feed, paper.db

    async def quote(self, session, request):
        async with session['lock']:
            settings = session['settings']
            market = self.feed.snapshot(settings.asset)
            leg = quote_single(market, settings, session['cash'], request)
            identifier, now = str(uuid.uuid4()), time.time()
            session['quotes'] = {k:v for k,v in session['quotes'].items() if now-v['created'] <= 10}
            quote = {'kind': 'manual', 'quote_id': identifier, 'created': now, 'expires_at': now+10,
                     'market_id': market['id'], 'asset': settings.asset, 'side': request.side,
                     'leg': leg, 'budget': settings.budget, 'paper': True,
                     'profit_if_win': float(D(leg['shares'])-D(leg['total_debit'])),
                     'max_loss': leg['total_debit'], 'cash_after': float(D(session['cash'])-D(leg['total_debit']))}
            session['quotes'][identifier] = copy.deepcopy(quote)
            return quote

    async def execute(self, session, request):
        async with session['lock']:
            prior = await self.db.trades.find_one({'session_id': session['id'], 'request_id': request.request_id}, {'_id': 0, 'session_id': 0})
            if prior:
                return prior
            quote = session['quotes'].get(request.quote_id)
            if not quote or quote.get('kind') != 'manual' or time.time() > quote['expires_at']:
                raise HTTPException(409, 'Manual quote expired. Refresh the quote before buying.')
            settings = session['settings'].model_copy(deep=True)
            if settings.asset != quote['asset']:
                raise HTTPException(409, 'Selected asset changed; request a new quote.')
            market = self.feed.snapshot(settings.asset)
            status, reason = side_integrity(market, quote['side'], settings)
            if status != 'LIVE' or market.get('id') != quote['market_id']:
                raise HTTPException(409, reason if status != 'LIVE' else 'Market rolled over. Refresh your quote.')
            side = quote['side']
            record = {'id': str(uuid.uuid4()), 'session_id': session['id'], 'request_id': request.request_id,
                      'created_at': utcnow(), 'asset': settings.asset, 'side': side, 'trade_type': 'MANUAL',
                      'market_id': market['id'], 'market_slug': market['slug'], 'market_end': market['end'],
                      'mode': 'PAPER', 'scenario': 'normal', 'objective': 'manual_directional',
                      'settings': settings.model_dump(), 'requested': {side: copy.deepcopy(quote['leg'])},
                      'actual': None, 'fills': {}, 'remaining_shares': {'up':0,'down':0},
                      'sale_proceeds':0, 'events':[], 'actual_debit':0, 'realized_pnl':None,
                      'order_type':'FOK single leg', 'atomic':False, 'directional_risk_accepted':True}
            self.paper.event(record, 'EXECUTING', f'Revalidating an intentional {side.upper()}-only paper purchase')
            await self.paper.save(session, record)
            await asyncio.sleep(settings.latency_ms/1000)
            market = self.feed.snapshot(settings.asset)
            state, reason = side_integrity(market, side, settings)
            filled = None
            if state == 'LIVE' and market.get('id') == quote['market_id'] and random.random() >= settings.failure_rate:
                filled = self.paper.fill(market, side, quote['leg'], settings, settings.liquidity_retention)
                if filled:
                    best = D(market['books'][side]['asks'][0][0])
                    if D(filled['highest_ask']) > best*(1+D(settings.max_slippage_pct)/100) or D(filled['total_debit']) > capital_cap(settings, session['cash']):
                        filled = None
            if not filled:
                self.paper.event(record, 'ABORTED', f'Manual buy aborted: {reason if state != "LIVE" else "price, liquidity, balance or order acceptance changed"}. No paper cash spent.')
            else:
                record['fills'][side] = filled
                record['remaining_shares'][side] = filled['shares']
                record['actual_debit'] = filled['total_debit']
                empty = walk([], 0, market['fee'])
                record['actual'] = payoff(filled if side=='up' else empty, filled if side=='down' else empty, settings.budget)
                session['cash'] = float(D(session['cash'])-D(filled['total_debit']))
                self.paper.event(record, 'OPEN_POSITION', f'{side.upper()} paper order filled. Intentional one-sided exposure; no automatic hedge or recovery.')
            await self.paper.save(session, record)
            await self.feed.notify()
            return self.paper.public(record)