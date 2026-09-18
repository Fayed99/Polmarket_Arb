import math
import time
from decimal import Decimal, ROUND_HALF_UP

import pytest
from pydantic import ValidationError

from books import Book, integrity
from models import LegacyOptimizerSettings as Settings
from optimizer import D, allowed, no_trade, optimize, payoff, walk


# Optimizer math, book integrity, and constraints
@pytest.fixture
def fee():
    return {"r": 0.07, "e": 1, "to": "c"}


@pytest.fixture
def live_market(fee):
    now_ms = time.time() * 1000
    return {
        "id": "mkt-1",
        "asset": "BTC",
        "start": time.time() - 60,
        "end": time.time() + 240,
        "connected": True,
        "fee": fee,
        "books": {
            "up": {
                "ready": True,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": 5.0,
                "size_precision": 2,
                "asks": [(0.45, 5.1), (0.46, 1.0)],
                "bids": [(0.44, 6.1)],
            },
            "down": {
                "ready": True,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": 5.0,
                "size_precision": 2,
                "asks": [(0.44, 5.1), (0.45, 1.0)],
                "bids": [(0.43, 6.1)],
            },
        },
    }


def test_walk_rounds_fees_to_5dp_and_walks_levels(fee):
    result = walk([(0.55, 3), (0.56, 4)], 5, fee, size_precision=2)
    expected_fee = (
        Decimal("3") * Decimal("0.07") * Decimal("0.55") * (1 - Decimal("0.55"))
        + Decimal("2") * Decimal("0.07") * Decimal("0.56") * (1 - Decimal("0.56"))
    ).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)
    assert result["fee"] == float(expected_fee)
    assert result["levels_consumed"] == 2
    assert result["shares"] == 5.0


def test_arbitrage_positive_when_both_asks_plus_fees_below_one(fee):
    up = walk([(0.45, 10)], 5, fee, 2)
    down = walk([(0.45, 10)], 5, fee, 2)
    result = payoff(up, down, 100)
    assert result["profit_up"] > 0 and result["profit_down"] > 0


def test_no_arbitrage_when_prices_and_fees_exceed_one(fee):
    up = walk([(0.70, 10)], 5, fee, 2)
    down = walk([(0.40, 10)], 5, fee, 2)
    result = payoff(up, down, 100)
    assert result["profit_up"] < 0 and result["profit_down"] < 0


def test_optimize_respects_max_deployment_and_loss(live_market):
    settings = Settings(budget=100, max_loss=1, objective="maximum_upside", max_deployment=10)
    result = optimize(live_market, settings, cash=1000)
    assert result["status"] == "READY"
    assert result["deployed"] <= 10
    assert abs(result["unspent"] - (settings.budget - result["deployed"])) < 1e-8


def test_optimize_matches_bruteforce_on_small_fixture(live_market):
    settings = Settings(budget=100, max_loss=1, objective="maximum_upside", max_deployment=10)
    model = optimize(live_market, settings, cash=1000)
    assert model["status"] == "READY"

    best = None
    q_values = [D("5") + D(i) * D("0.01") for i in range(0, 21)]
    for uq in q_values:
        for dq in q_values:
            up = walk(live_market["books"]["up"]["asks"], uq, live_market["fee"], 2)
            down = walk(live_market["books"]["down"]["asks"], dq, live_market["fee"], 2)
            if not up or not down:
                continue
            candidate = payoff(up, down, settings.budget)
            if allowed(candidate, settings, 1000):
                if best is None or (candidate["best"], -candidate["deployed"]) > (best["best"], -best["deployed"]):
                    best = candidate
    assert best is not None
    # If this fails, solver search or constraints may be excluding a better feasible point.
    assert model["best"] >= best["best"] - 1e-6


def test_no_trade_zero_baseline_when_constraints_impossible(live_market):
    settings = Settings(budget=100, max_loss=0, objective="guaranteed_profit", min_profit=50)
    result = optimize(live_market, settings, cash=1000)
    assert result["status"] == "NO_TRADE"
    assert result["deployed"] == 0 and result["unspent"] == settings.budget


def test_settings_reject_nan_inputs():
    with pytest.raises(ValidationError):
        Settings(budget=float("nan"))


def test_book_tick_and_zero_removal_and_monotonicity():
    b = Book("token-1", "0.01", 5)
    stamp = int(time.time() * 1000)
    b.snapshot(
        {
            "timestamp": stamp,
            "tick_size": "0.01",
            "min_order_size": "5",
            "hash": "h1",
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.60", "size": "10"}],
        }
    )
    with pytest.raises(ValueError):
        b.level("0.605", "1")
    b.change({"side": "SELL", "price": "0.60", "size": "0", "hash": "h2"}, stamp + 1)
    assert (0.6 in b.asks) is False
    with pytest.raises(ValueError):
        b.change({"side": "BUY", "price": "0.40", "size": "1"}, stamp)


def test_integrity_reports_stale_and_unsynced(live_market):
    settings = Settings(stale_ms=500, sync_ms=100)
    stale_market = {**live_market, "books": {"up": {**live_market["books"]["up"]}, "down": {**live_market["books"]["down"]}}}
    stale_market["books"]["up"]["arrival_ms"] -= 1000
    status, _ = integrity(stale_market, settings)
    assert status == "STALE"

    now_ms = time.time() * 1000
    unsynced_settings = Settings(stale_ms=5000, sync_ms=100)
    unsynced = {**live_market, "books": {"up": {**live_market["books"]["up"]}, "down": {**live_market["books"]["down"]}}}
    unsynced["books"]["up"]["event_ms"] = now_ms
    unsynced["books"]["up"]["arrival_ms"] = now_ms
    unsynced["books"]["down"]["event_ms"] = now_ms - 500
    unsynced["books"]["down"]["arrival_ms"] = now_ms
    status, _ = integrity(unsynced, unsynced_settings)
    assert status == "UNSYNCED"


def test_no_trade_has_expected_baseline_fields():
    settings = Settings(budget=123.45)
    result = no_trade(settings)
    assert result["deployed"] == 0 and result["unspent"] == 123.45
