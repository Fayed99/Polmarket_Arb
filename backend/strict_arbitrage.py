"""Minimum equal-quantity arbitrage over actual depth, with exact final checks.

Unrounded piecewise costs bound the rounded fee cost to +/-0.00001.
Necessary-condition intervals prune impossible sizes, then exact candidates
are checked in ascending order. We never binary-search global profitability.
"""
import bisect
import time
from decimal import ROUND_CEILING, ROUND_DOWN
from books import integrity
from execution_math import D, walk, payoff, value, rounded_fee
from strict_policy import STRATEGY, POLICY_VERSION, REQUIRED_PROFIT, MIN_TOTAL_COST, public_policy
from quote_store import digest, market_binding


def capital_cap(settings, cash):
    return min(D(settings.budget), D(cash), D(settings.max_deployment) if settings.max_deployment is not None else D(settings.budget))


def validate_pair(up, down, settings, cash, envelope=None, expected_q=None):
    if up is None or down is None:
        return False, 'INSUFFICIENT_DEPTH'
    uq, dq = value(up,'shares'), value(down,'shares')
    if uq <= 0 or uq != dq or (expected_q is not None and uq != D(expected_q)):
        return False, 'UNEQUAL_QUANTITIES'
    debit = value(up,'total_debit') + value(down,'total_debit')
    if debit < MIN_TOTAL_COST:
        return False, 'BELOW_MINIMUM_COST'
    if uq-debit < REQUIRED_PROFIT:
        return False, 'BELOW_REQUIRED_PROFIT'
    if debit > capital_cap(settings, cash):
        return False, 'CAPITAL_LIMIT'
    if envelope:
        if debit > D(envelope['combined_max_debit']):
            return False, 'ECONOMIC_ENVELOPE'
        for side, leg in (('up',up),('down',down)):
            if value(leg,'total_debit') > D(envelope[side]['max_debit']) or value(leg,'highest_ask') > D(envelope[side]['limit_price']):
                return False, 'ECONOMIC_ENVELOPE'
    return True, 'QUALIFIED'


class Curve:
    def __init__(self, book, fee, slippage):
        best = D(book['asks'][0][0])
        limit = best*(1+D(slippage)/100)
        self.levels = [(D(p),D(q)) for p,q in book['asks'] if D(p)<=limit and D(q)>0]
        self.ends, self.costs, self.fees = [], [D(0)], [D(0)]
        quantity = D(0)
        self.rate = D(fee['r'])
        for p,q in self.levels:
            quantity += q
            self.ends.append(quantity)
            self.costs.append(self.costs[-1]+p*q)
            self.fees.append(self.fees[-1]+q*self.rate*p*(1-p))

    def amounts(self, q):
        index = min(bisect.bisect_left(self.ends, q), len(self.levels)-1)
        p = self.levels[index][0]
        before = self.ends[index-1] if index else D(0)
        remaining = q-before
        return self.costs[index]+remaining*p, self.fees[index]+remaining*self.rate*p*(1-p)

    def raw(self, q):
        cost, fee = self.amounts(q)
        return cost+fee

    def debit(self, q):
        cost, fee = self.amounts(q)
        return cost+rounded_fee(fee)


def _ge_interval(low, high, slope, intercept, target):
    if slope > 0:
        low = max(low, (target-intercept)/slope)
    elif slope < 0:
        high = min(high, (target-intercept)/slope)
    elif intercept < target:
        return None
    return (low,high) if low <= high else None


def economic_envelope(market, legs, q, settings, cash):
    cap = min(capital_cap(settings,cash), q-REQUIRED_PROFIT)
    costs = {s:value(legs[s],'total_debit') for s in ('up','down')}
    slack = cap-costs['up']-costs['down']
    up_cap = costs['up']+(slack/2).quantize(D('0.000001'), rounding=ROUND_DOWN)
    caps = {'up':up_cap, 'down':cap-up_cap}
    envelope = {'combined_max_debit':str(cap), 'quantity':str(q)}
    rate = D(market['fee']['r'])
    for side in ('up','down'):
        leg, book = legs[side], market['books'][side]
        tick = D(book['tick'])
        prefix_cost, prefix_fee = D(0), D(0)
        for fill in leg['fills'][:-1]:
            p, size = D(fill['exact']['price']), D(fill['exact']['shares'])
            prefix_cost += p*size
            prefix_fee += size*rate*p*(1-p)
        remaining = D(leg['fills'][-1]['exact']['shares'])
        lo = int(value(leg,'highest_ask')/tick)
        hi = int((1-tick)/tick)
        while lo < hi:
            mid = (lo+hi+1)//2
            p = D(mid)*tick
            cost = prefix_cost+remaining*p+rounded_fee(prefix_fee+remaining*rate*p*(1-p))
            if cost <= caps[side]:
                lo = mid
            else:
                hi = mid-1
        economic_price = D(lo)*tick
        slippage_price = (value(leg,'best_ask')*(1+D(settings.max_slippage_pct)/100)/tick).to_integral_value(rounding=ROUND_DOWN)*tick
        envelope[side] = {'max_debit':str(caps[side]), 'economic_price_cap':str(economic_price),
                          'limit_price':str(min(economic_price,slippage_price)),
                          'reference_best_ask':str(value(leg,'best_ask'))}
    return envelope


def no_trade(settings, status='NO_TRADE', reason='No equal-quantity trade meets the $1.00 cost and $0.12 net-profit requirements.'):
    return {'status':status, 'reason':reason, 'strategy':STRATEGY, 'policy':public_policy(),
            'budget':settings.budget, 'deployed':0, 'unspent':settings.budget,
            'q':0, 'guaranteed_payout':0, 'guaranteed_profit':0, 'up':None, 'down':None,
            'fees':0, 'worst':0, 'best':0, 'profit_up':0, 'profit_down':0,
            'payout_up':0, 'payout_down':0, 'worst_roi':0, 'best_roi':0, 'computed_at':time.time()}


def optimize(market, settings, cash):
    if settings.paused:
        return no_trade(settings,'PAUSED','Strict arbitrage paused; manual directional paper trades remain separate.')
    state, reason = integrity(market, settings)
    if state != 'LIVE':
        return no_trade(settings,state,reason)
    fee = market.get('fee',{})
    if D(fee.get('e',-1)) != 1 or not 0 <= D(fee.get('r',-1)) <= 1:
        return no_trade(settings,'UNAVAILABLE','Unsupported fee configuration; no trade authorized.')
    cap = capital_cap(settings,cash)
    if cap < MIN_TOTAL_COST:
        return no_trade(settings,reason='Available capital is below the $1.00 minimum combined cost.')
    books = market['books']
    step = max(D(10)**-books[s]['size_precision'] for s in ('up','down'))
    minimum = max(D(books[s]['minimum']) for s in ('up','down'))
    q_min = (minimum/step).to_integral_value(rounding=ROUND_CEILING)*step
    curves = {s:Curve(books[s],fee,settings.max_slippage_pct) for s in ('up','down')}
    if any(not curve.ends for curve in curves.values()):
        return no_trade(settings,reason='Insufficient executable depth within slippage limits.')
    q_max = (min(c.ends[-1] for c in curves.values())/step).to_integral_value(rounding=ROUND_DOWN)*step
    if q_max < q_min:
        return no_trade(settings,reason='Depth cannot fill both exchange minimum quantities equally.')
    raw = lambda q: sum((curve.raw(q) for curve in curves.values()), D(0))
    # Include exact depth breakpoints; fee rounding uncertainty is at most 0.00001 combined.
    bounds = sorted({q_min,q_max,*(x for c in curves.values() for x in c.ends if q_min<x<q_max)})
    intervals = list(zip(bounds,bounds[1:])) if len(bounds)>1 else [(q_min,q_min)]
    epsilon, checked, last = D('0.00001'), 0, q_min-step
    deadline = time.monotonic()+0.2
    for left,right in intervals:
        slope = (raw(right)-raw(left))/(right-left) if right>left else D(0)
        intercept = raw(left)-slope*left
        possible = (left,right)
        for a,b,target in ((slope,intercept,MIN_TOTAL_COST-epsilon),
                            (-slope,-intercept,-cap-epsilon),
                            (1-slope,-intercept,REQUIRED_PROFIT-epsilon)):
            possible = _ge_interval(*possible,a,b,target) if possible else None
        if not possible:
            continue
        # Expand outward by at most one grid point, so division rounding cannot skip a boundary.
        q = max(q_min,last+step,(possible[0]/step).to_integral_value(rounding=ROUND_DOWN)*step)
        end = min(q_max,(possible[1]/step).to_integral_value(rounding=ROUND_CEILING)*step)
        while q <= end:
            checked += 1
            if checked > 20000 or time.monotonic()>deadline:
                return no_trade(settings,'CALCULATION_LIMIT','Minimum quantity could not be proven within the calculation limit. No trade authorized.')
            debit = sum((c.debit(q) for c in curves.values()),D(0))
            last = q
            if MIN_TOTAL_COST <= debit <= cap and q-debit >= REQUIRED_PROFIT:
                legs = {s:walk(curves[s].levels,q,fee,books[s]['size_precision']) for s in ('up','down')}
                valid,_ = validate_pair(legs['up'],legs['down'],settings,cash,expected_q=q)
                if valid:
                    envelope = economic_envelope(market,legs,q,settings,cash)
                    valid,_ = validate_pair(legs['up'],legs['down'],settings,cash,envelope,q)
                    if not valid:
                        return no_trade(settings,'UNAVAILABLE','Economic envelope could not be verified.')
                    result = payoff(legs['up'],legs['down'],settings.budget)
                    binding = market_binding(market)
                    identifier = digest({'binding':binding,'q':str(q),'envelope':envelope,
                                         'settings':settings.model_dump(),'policy':POLICY_VERSION,
                                         'costs':[str(value(legs[s],'total_debit')) for s in ('up','down')]})
                    return {**result, 'status':'READY','strategy':STRATEGY,'policy':public_policy(),
                            'q':str(q),'budget':settings.budget,'market_id':market['id'],
                            'classification':'Strict paired arbitrage',
                            'reason':'Smallest qualifying equal quantity; profit is conditional on both legs filling.',
                            'decision_id':identifier,'envelope':envelope,'computed_at':time.time(),
                            'minimality_proven':True}
            q += step
    return no_trade(settings)