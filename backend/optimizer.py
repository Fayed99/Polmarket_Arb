"""Integer-quantity optimizer over convex, piecewise-linear executable ask costs.

HiGHS searches both upside directions, or the max-min payoff. Decimal walks
revalidate each solution, with per-level V2 fees. No-trade is the zero baseline.
"""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_CEILING
import time
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from books import integrity

D = lambda x: Decimal(str(x))


def walk(levels, quantity, fee, size_precision=2, selling=False):
    step = D(10) ** -size_precision
    q = D(quantity).quantize(step, rounding=ROUND_DOWN)
    remaining, cost, fees, fills = q, D(0), D(0), []
    for price, size in sorted(levels, reverse=selling):
        p, available = D(price), D(size)
        take = min(remaining, available)
        if take <= 0:
            continue
        level_fee = take * D(fee['r']) * p * (1 - p)
        cost += take * p
        fees += level_fee
        fills.append({'price': float(p), 'shares': float(take), 'fee': float(level_fee)})
        remaining -= take
        if remaining == 0:
            break
    if remaining > 0:
        return None
    fees = fees.quantize(D('0.00001'), rounding=ROUND_HALF_UP)
    vwap = cost / q if q else D(0)
    best = D(sorted(levels, reverse=selling)[0][0]) if levels else D(0)
    return {'shares': float(q), 'purchase_cost': float(cost), 'fee': float(fees),
            'total_debit': float(cost + fees), 'vwap': float(vwap),
            'best_ask': float(best),
            'highest_ask': max((f['price'] for f in fills), default=0),
            'slippage_pct': float(abs(vwap - best) / best * 100) if best else 0,
            'levels_consumed': len(fills), 'fills': fills,
            'net_proceeds': float(cost - fees) if selling else 0}


def payoff(up, down, budget):
    debit = D(up['total_debit']) + D(down['total_debit'])
    p_up, p_down = D(up['shares']) - debit, D(down['shares']) - debit
    return {'up': up, 'down': down, 'deployed': float(debit), 'unspent': float(D(budget) - debit),
            'fees': float(D(up['fee']) + D(down['fee'])), 'profit_up': float(p_up),
            'profit_down': float(p_down), 'payout_up': up['shares'], 'payout_down': down['shares'],
            'worst': float(min(p_up, p_down)), 'best': float(max(p_up, p_down)),
            'worst_roi': float(min(p_up, p_down) / debit * 100) if debit else 0,
            'best_roi': float(max(p_up, p_down) / debit * 100) if debit else 0}


def allowed(result, settings, cash):
    objective = settings.custom_objective if settings.objective == 'custom' else settings.objective
    cap = min(settings.budget, cash, settings.max_deployment if settings.max_deployment is not None else settings.budget)
    if D(result['deployed']) > D(cap) or result['deployed'] <= 0:
        return False
    for side in ('up', 'down'):
        leg = result[side]
        if leg['shares'] <= 0:
            return False
        if leg.get('best_ask') and D(leg['highest_ask']) > D(leg['best_ask']) * (1 + D(settings.max_slippage_pct) / 100):
            return False
    if objective in ('guaranteed_profit', 'max_worst_case'):
        if result['worst'] < (settings.min_profit if objective == 'guaranteed_profit' else 0.000001):
            return False
    elif D(result['worst']) < -D(settings.max_loss) or result['best'] < settings.min_profit:
        return False
    if settings.objective == 'custom':
        share = result['up']['shares'] / (result['up']['shares'] + result['down']['shares']) * 100
        if not settings.min_up_share_pct - 1e-8 <= share <= settings.max_up_share_pct + 1e-8:
            return False
    return True


def no_trade(settings, status='NO_TRADE', reason='No allocation meets your objective after fees and execution constraints.'):
    return {'status': status, 'reason': reason, 'budget': settings.budget, 'deployed': 0,
            'unspent': settings.budget, 'worst': 0, 'best': 0, 'fees': 0, 'up': None, 'down': None,
            'profit_up': 0, 'profit_down': 0, 'payout_up': 0, 'payout_down': 0,
            'worst_roi': 0, 'best_roi': 0, 'computed_at': time.time(), 'solver': 'HiGHS integer quantities'}


def optimize(market, settings, cash):
    if settings.paused:
        return no_trade(settings, 'PAUSED', 'Optimizer paused. Capital remains in cash.')
    status, reason = integrity(market, settings)
    if status != 'LIVE':
        return no_trade(settings, status, reason)
    cap = min(settings.budget, cash, settings.max_deployment if settings.max_deployment is not None else settings.budget)
    if cap <= 0:
        return no_trade(settings, reason='No available capital within your deployment limit.')
    obj = settings.custom_objective if settings.objective == 'custom' else settings.objective
    # Variables: UP quantity units, DOWN quantity units, UP debit, DOWN debit, minimum profit.
    rows, upper = [], []
    lower_bounds, upper_bounds = [0, 0, 0, 0, -np.inf], [np.inf] * 5
    steps, ladders = [], []
    for i, side in enumerate(('up', 'down')):
        book = market['books'][side]
        step = 10 ** -book['size_precision']
        steps.append(step)
        best = book['asks'][0][0]
        ladder = [(p, q) for p, q in book['asks'] if p <= best * (1 + settings.max_slippage_pct / 100) + 1e-10]
        ladders.append(ladder)
        cumulative_q = cumulative_cost = 0
        previous = 0
        for p, q in ladder:
            slope = p + market['fee']['r'] * p * (1 - p)
            if slope < previous:
                return no_trade(settings, 'UNAVAILABLE', 'Non-convex fee curve is not supported')
            previous = slope
            row = [0.0] * 5
            row[i], row[i + 2] = slope * step, -1
            rows.append(row)
            upper.append(slope * cumulative_q - cumulative_cost)
            cumulative_q += q
            cumulative_cost += slope * q
        lower_bounds[i] = int((D(book['minimum']) / D(step)).to_integral_value(rounding=ROUND_CEILING))
        exact_liquidity = sum((D(q) for _, q in ladder), D(0))
        upper_bounds[i] = int((exact_liquidity / D(step)).to_integral_value(rounding=ROUND_DOWN))
        if upper_bounds[i] < lower_bounds[i]:
            return no_trade(settings, reason='Insufficient executable liquidity above minimum order size.')
    # Roundoff reserve ensures rounded fees cannot violate the pool/loss constraints.
    rows.append([0, 0, 1, 1, 0]); upper.append(cap - 0.00002)
    for i in range(2):
        row = [0, 0, 1, 1, 1]
        row[i] = -steps[i]
        rows.append(row); upper.append(0)
    floor = settings.min_profit if obj == 'guaranteed_profit' else (0 if obj == 'max_worst_case' else -settings.max_loss)
    lower_bounds[4] = floor + 0.00002
    if settings.objective == 'custom':
        lo, hi = settings.min_up_share_pct / 100, settings.max_up_share_pct / 100
        rows.extend([[-(1-lo)*steps[0], lo*steps[1], 0, 0, 0], [(1-hi)*steps[0], -hi*steps[1], 0, 0, 0]])
        upper.extend([0, 0])
    objectives = [[0, 0, 1e-8, 1e-8, -1]] if obj in ('guaranteed_profit', 'max_worst_case') else [
        [-steps[0], 0, 1, 1, 0], [0, -steps[1], 1, 1, 0]]
    candidates = []
    for objective in objectives:
        result = milp(c=np.array(objective), integrality=np.array([1, 1, 0, 0, 0]),
                      bounds=Bounds(lower_bounds, upper_bounds),
                      constraints=LinearConstraint(np.array(rows), -np.inf, upper),
                      options={'time_limit': 0.3, 'mip_rel_gap': 0.000001})
        if result.x is None:
            continue
        fills = [walk(ladders[i], D(round(result.x[i])) * D(steps[i]), market['fee'], market['books'][side]['size_precision'])
                 for i, side in enumerate(('up', 'down'))]
        if not all(fills):
            continue
        candidate = payoff(*fills, settings.budget)
        if allowed(candidate, settings, cash):
            candidates.append(candidate)
    if not candidates:
        return no_trade(settings)
    score = 'worst' if obj in ('guaranteed_profit', 'max_worst_case') else 'best'
    best = max(candidates, key=lambda c: (c[score], -c['deployed']))
    pure = best['worst'] >= settings.min_profit
    return {**best, 'status': 'READY', 'budget': settings.budget,
            'classification': 'Guaranteed payoff' if pure else 'Asymmetric risk / reward',
            'reason': 'Both outcomes profitable after fees. Two-leg execution is not atomic.' if pure else 'A calculated allocation within your loss limit. No direction prediction.',
            'computed_at': time.time(), 'solver': 'HiGHS integer quantities', 'market_id': market['id']}