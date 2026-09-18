from decimal import Decimal

STRATEGY = 'strict_arbitrage'
POLICY_VERSION = 'strict-arbitrage-v1'
PROFIT_FLOOR = Decimal('0.10')
SAFETY_MARGIN = Decimal('0.02')
REQUIRED_PROFIT = PROFIT_FLOOR + SAFETY_MARGIN
MIN_TOTAL_COST = Decimal('1.00')


def public_policy():
    return {'strategy': STRATEGY, 'version': POLICY_VERSION, 'profit_floor': str(PROFIT_FLOOR),
            'safety_margin': str(SAFETY_MARGIN), 'required_profit': str(REQUIRED_PROFIT),
            'minimum_total_cost': str(MIN_TOTAL_COST), 'sizing': 'smallest_qualifying_q'}