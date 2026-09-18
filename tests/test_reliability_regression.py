import json
import time
import uuid
from decimal import Decimal

import pytest
import requests

from models import Settings
from optimizer import allowed, optimize, payoff, walk


# SSE reliability and optimizer regression coverage for public deployment
def test_sse_30s_rotation_reconnect_and_health_responsive(api_url):
    sid = str(uuid.uuid4())
    stream_url = f"{api_url}/stream"

    seen_payloads = 0
    stream_start = time.monotonic()

    with requests.get(
        stream_url,
        params={"session_id": sid},
        stream=True,
        timeout=(10, 45),
    ) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")

        health = requests.get(f"{api_url}/health", timeout=20)
        assert health.status_code == 200
        assert health.json().get("status") == "ok"

        for raw in res.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data: "):
                payload = json.loads(raw[6:])
                assert "diagnostics" in payload
                seen_payloads += 1
            if time.monotonic() - stream_start > 40:
                pytest.fail("SSE stream exceeded expected bounded lifetime")

    stream_duration = time.monotonic() - stream_start
    assert seen_payloads >= 2
    assert 26 <= stream_duration <= 36

    reconnect_start = time.monotonic()
    first_reconnect_data = None
    with requests.get(
        stream_url,
        params={"session_id": sid},
        stream=True,
        timeout=(10, 20),
    ) as res2:
        assert res2.status_code == 200
        for raw in res2.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data: "):
                first_reconnect_data = json.loads(raw[6:])
                break

    assert first_reconnect_data is not None
    assert (time.monotonic() - reconnect_start) < 5


def _market_fixture_multi_level():
    now_ms = time.time() * 1000
    return {
        "id": "mkt-regression-1",
        "asset": "BTC",
        "start": time.time() - 60,
        "end": time.time() + 240,
        "connected": True,
        "fee": {"r": 0.07, "e": 1, "to": "c"},
        "books": {
            "up": {
                "ready": True,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": 5.0,
                "size_precision": 2,
                "asks": [(0.45, 5.12), (0.46, 4.88), (0.47, 10.0)],
                "bids": [(0.44, 10.0)],
            },
            "down": {
                "ready": True,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": 5.0,
                "size_precision": 2,
                "asks": [(0.44, 6.34), (0.45, 3.66), (0.46, 10.0)],
                "bids": [(0.43, 10.0)],
            },
        },
    }


def _score(candidate, settings):
    objective = settings.custom_objective if settings.objective == "custom" else settings.objective
    return candidate["worst"] if objective in ("guaranteed_profit", "max_worst_case") else candidate["best"]


def _bruteforce_best(market, settings, cash):
    best = None
    q_values = [Decimal("5") + Decimal(i) * Decimal("0.01") for i in range(0, 61)]
    for uq in q_values:
        for dq in q_values:
            up = walk(market["books"]["up"]["asks"], uq, market["fee"], 2)
            down = walk(market["books"]["down"]["asks"], dq, market["fee"], 2)
            if not up or not down:
                continue
            candidate = payoff(up, down, settings.budget)
            if not allowed(candidate, settings, cash):
                continue
            if best is None:
                best = candidate
                continue
            if (_score(candidate, settings), -candidate["deployed"]) > (
                _score(best, settings),
                -best["deployed"],
            ):
                best = candidate
    return best


@pytest.mark.parametrize(
    "settings,cash",
    [
        (Settings(budget=100, max_loss=1, objective="controlled_loss", max_slippage_pct=3, max_deployment=7), 1000),
        (Settings(budget=100, min_profit=0.01, objective="guaranteed_profit", max_slippage_pct=3, max_deployment=8), 1000),
        (Settings(budget=100, objective="max_worst_case", max_slippage_pct=3, max_deployment=9), 1000),
        (
            Settings(
                budget=100,
                objective="custom",
                custom_objective="maximum_upside",
                min_up_share_pct=50,
                max_up_share_pct=60,
                max_slippage_pct=3,
                max_deployment=9,
            ),
            8,
        ),
    ],
)
def test_optimizer_bruteforce_multiobjective_and_decimal_shares(settings, cash):
    market = _market_fixture_multi_level()
    model = optimize(market, settings, cash=cash)
    brute = _bruteforce_best(market, settings, cash)

    assert brute is not None
    assert model["status"] == "READY"

    cap = min(
        settings.budget,
        cash,
        settings.max_deployment if settings.max_deployment is not None else settings.budget,
    )
    assert model["deployed"] <= cap

    step = Decimal("0.01")
    for side in ("up", "down"):
        shares = Decimal(str(model[side]["shares"]))
        assert shares >= Decimal("5")
        assert (shares / step).to_integral_value() == shares / step

    assert _score(model, settings) >= _score(brute, settings) - 1e-6

    if settings.objective == "custom":
        up_share_pct = model["up"]["shares"] / (model["up"]["shares"] + model["down"]["shares"]) * 100
        assert settings.min_up_share_pct - 1e-8 <= up_share_pct <= settings.max_up_share_pct + 1e-8
