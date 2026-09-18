"""Shared execution math; Decimal values stay exact until presentation serialization."""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

D = lambda x: Decimal(str(x))
FEE_QUANTUM = D('0.00001')


def value(leg, field):
    return D(leg.get('exact', {}).get(field, leg.get(field, 0)))


def rounded_fee(raw):
    return raw.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)


def walk(levels, quantity, fee, size_precision=2, selling=False):
    step = D(10) ** -size_precision
    quantity = D(quantity).quantize(step, rounding=ROUND_DOWN)
    remaining, cost, fees, fills = quantity, D(0), D(0), []
    ordered = sorted((D(p), D(q)) for p,q in levels)
    if selling:
        ordered.reverse()
    for price, available in ordered:
        take = min(remaining, available)
        if take <= 0:
            continue
        fee_raw = take * D(fee['r']) * price * (1-price)
        cost += take * price
        fees += fee_raw
        fills.append({'price': float(price), 'shares': float(take), 'fee': float(fee_raw),
                      'exact': {'price': str(price), 'shares': str(take), 'fee': str(fee_raw)}})
        remaining -= take
        if remaining == 0:
            break
    if remaining > 0:
        return None
    fees = rounded_fee(fees)
    vwap = cost / quantity if quantity else D(0)
    best = ordered[0][0] if ordered else D(0)
    exact = {'shares': quantity, 'purchase_cost': cost, 'fee': fees, 'total_debit': cost+fees,
             'vwap': vwap, 'best_ask': best,
             'highest_ask': max((D(f['exact']['price']) for f in fills), default=D(0)),
             'net_proceeds': cost-fees if selling else D(0)}
    return {**{k:float(v) for k,v in exact.items()}, 'exact': {k:str(v) for k,v in exact.items()},
            'slippage_pct': float(abs(vwap-best)/best*100) if best else 0,
            'levels_consumed': len(fills), 'fills': fills}


def payoff(up, down, budget):
    debit = value(up, 'total_debit') + value(down, 'total_debit')
    uq, dq = value(up, 'shares'), value(down, 'shares')
    pu, pd = uq-debit, dq-debit
    guaranteed = min(uq, dq)
    exact = {'deployed': debit, 'unspent': D(budget)-debit,
             'fees': value(up,'fee')+value(down,'fee'), 'profit_up': pu, 'profit_down': pd,
             'payout_up': uq, 'payout_down': dq, 'worst': min(pu,pd), 'best': max(pu,pd),
             'guaranteed_payout': guaranteed, 'guaranteed_profit': guaranteed-debit,
             'unmatched_up': uq-guaranteed, 'unmatched_down': dq-guaranteed}
    return {**{k:float(v) for k,v in exact.items()}, 'exact': {k:str(v) for k,v in exact.items()},
            'up': up, 'down': down, 'worst_roi': float(min(pu,pd)/debit*100) if debit else 0,
            'best_roi': float(max(pu,pd)/debit*100) if debit else 0}