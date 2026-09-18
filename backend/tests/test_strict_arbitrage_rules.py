import math
import time
from decimal import Decimal

import pytest

from execution_math import D, walk
from models import Settings
from strict_arbitrage import optimize, validate_pair


# Strict arbitrage objective and envelope checks
def _market(asks_up, asks_down, fee_rate=0.07, minimum=5.0, size_precision=2, tick="0.01"):
    now_ms = time.time() * 1000
    return {
        "id": "strict-mkt-1",
        "condition_id": "strict-cond-1",
        "slug": "strict-btc-updown-5m",
        "asset": "BTC",
        "start": time.time() - 60,
        "end": time.time() + 240,
        "connected": True,
        "fee": {"r": fee_rate, "e": 1, "to": "c"},
        "books": {
            "up": {
                "ready": True,
                "token_id": "up-token",
                "tick": tick,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": minimum,
                "size_precision": size_precision,
                "asks": asks_up,
                "bids": [(asks_up[0][0] - 0.01, 100.0)],
            },
            "down": {
                "ready": True,
                "token_id": "down-token",
                "tick": tick,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": minimum,
                "size_precision": size_precision,
                "asks": asks_down,
                "bids": [(asks_down[0][0] - 0.01, 100.0)],
            },
        },
    }


def _leg(shares: str, debit: str, ask: str = "0.50"):
    return {
        "shares": float(shares),
        "total_debit": float(debit),
        "highest_ask": float(ask),
        "best_ask": float(ask),
        "exact": {
            "shares": shares,
            "total_debit": debit,
            "highest_ask": ask,
            "best_ask": ask,
        },
    }


def test_validate_pair_strict_threshold_boundaries():
    settings = Settings(budget=100)

    ok, reason = validate_pair(_leg("1.12", "0.50"), _leg("1.12", "0.50"), settings, cash=100)
    assert ok is True
    assert reason == "QUALIFIED"

    ok, reason = validate_pair(_leg("1.12", "0.500005"), _leg("1.12", "0.500005"), settings, cash=100)
    assert ok is False
    assert reason == "BELOW_REQUIRED_PROFIT"

    ok, reason = validate_pair(_leg("1.12", "0.499995"), _leg("1.12", "0.499995"), settings, cash=100)
    assert ok is False
    assert reason == "BELOW_MINIMUM_COST"


def test_validate_pair_rejects_unequal_quantities():
    settings = Settings(budget=100)
    ok, reason = validate_pair(_leg("5.00", "2.20", ask="0.44"), _leg("5.01", "2.20", ask="0.44"), settings, cash=100)
    assert ok is False
    assert reason == "UNEQUAL_QUANTITIES"


def test_optimize_chooses_smallest_qualifying_q_even_when_larger_q_more_profitable():
    market = _market([(0.40, 100.0)], [(0.40, 100.0)], fee_rate=0.0)
    settings = Settings(budget=1000, max_slippage_pct=2)
    result = optimize(market, settings, cash=1000)

    assert result["status"] == "READY"
    assert D(result["q"]) == D("5")
    assert D(result["guaranteed_profit"]) == D("1")


def test_optimize_capital_limit_blocks_when_cap_below_minimum_total_cost():
    market = _market([(0.45, 20.0)], [(0.45, 20.0)], fee_rate=0.0)
    settings = Settings(budget=0.99)
    result = optimize(market, settings, cash=0.99)

    assert result["status"] == "NO_TRADE"
    assert "below the $1.00 minimum" in result["reason"]


def test_envelope_combined_sum_and_tick_alignment():
    market = _market([(0.44, 20.0), (0.45, 20.0)], [(0.44, 20.0), (0.45, 20.0)], fee_rate=0.07)
    settings = Settings(budget=100)
    result = optimize(market, settings, cash=100)

    assert result["status"] == "READY"
    env = result["envelope"]
    q = D(result["q"])
    combined = D(env["combined_max_debit"])

    assert combined <= q - D("0.12")
    assert combined <= D("100")

    up_max = D(env["up"]["max_debit"])
    down_max = D(env["down"]["max_debit"])
    assert up_max + down_max == combined

    tick = D(market["books"]["up"]["tick"])
    for side in ("up", "down"):
        limit_price = D(env[side]["limit_price"])
        econ_cap = D(env[side]["economic_price_cap"])
        assert limit_price <= econ_cap
        assert (limit_price / tick).to_integral_value() == limit_price / tick


def test_nonmonotonic_feasibility_interval_appears_then_disappears_and_smallest_q_wins():
    market = _market([(0.30, 5.0), (0.99, 5.0)], [(0.30, 5.0), (0.99, 5.0)], fee_rate=0.0)
    settings = Settings(budget=100)

    feasible = []
    for i in range(0, 501):
        q = D("5") + D(i) * D("0.01")
        up = walk(market["books"]["up"]["asks"], q, market["fee"], 2)
        down = walk(market["books"]["down"]["asks"], q, market["fee"], 2)
        if not up or not down:
            continue
        ok, _ = validate_pair(up, down, settings, cash=1000, expected_q=q)
        feasible.append((q, ok))

    assert any(ok for _, ok in feasible)
    assert any((not ok) and q > D("6.95") for q, ok in feasible)

    result = optimize(market, settings, cash=1000)
    assert result["status"] == "READY"
    assert D(result["q"]) == D("5")


def test_optimize_outputs_no_nan_or_negative_core_values():
    market = _market([(0.44, 20.0)], [(0.44, 20.0)], fee_rate=0.07)
    settings = Settings(budget=100)
    result = optimize(market, settings, cash=100)

    assert result["status"] == "READY"
    numeric_fields = [
        result["deployed"],
        result["guaranteed_payout"],
        result["guaranteed_profit"],
        result["up"]["shares"],
        result["down"]["shares"],
    ]
    for value in numeric_fields:
        assert not math.isnan(float(value))
        assert float(value) >= 0
