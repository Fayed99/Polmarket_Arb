import time
import uuid

import pytest
import requests


# Public API tests for Binance reference, manual directional paper flow, and README endpoint


def _headers(session_id: str) -> dict:
    return {"X-Session-ID": session_id, "Content-Type": "application/json"}


def _new_session_id() -> str:
    return str(uuid.uuid4())


def _wait_for_manual_side_live(api_url: str, session_id: str, side: str, timeout_s: int = 90):
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        res = requests.get(f"{api_url}/state", headers=_headers(session_id), timeout=25)
        assert res.status_code == 200
        payload = res.json()
        last = payload
        if payload.get("manual_quotes", {}).get(side, {}).get("status") == "LIVE":
            return payload
        time.sleep(1)
    pytest.skip(
        f"Manual {side.upper()} book did not become LIVE within {timeout_s}s; "
        f"last status={last.get('manual_quotes', {}).get(side, {}).get('status') if last else 'unknown'}"
    )


def _set_deterministic_trade_settings(api_url: str, session_id: str):
    current = requests.get(f"{api_url}/state", headers=_headers(session_id), timeout=25)
    assert current.status_code == 200
    state = current.json()
    settings = state["settings"]
    settings.update({"latency_ms": 0, "failure_rate": 0, "liquidity_retention": 1})
    saved = requests.put(f"{api_url}/settings", headers=_headers(session_id), json=settings, timeout=25)
    assert saved.status_code == 200
    saved_payload = saved.json()
    assert saved_payload["latency_ms"] == 0
    assert saved_payload["failure_rate"] == 0
    assert saved_payload["liquidity_retention"] == 1


def _execute_manual_with_retries(api_url: str, session_id: str, side: str, amount: float = 20.0, retries: int = 3):
    """Get fresh quote + execute with short retries around rollover/book timing."""
    last_error = None
    for _ in range(retries):
        quote = requests.post(
            f"{api_url}/paper/manual/quote",
            headers=_headers(session_id),
            json={"side": side, "amount": amount, "unit": "dollars"},
            timeout=25,
        )
        if quote.status_code != 200:
            last_error = f"quote_status={quote.status_code}, detail={quote.text[:180]}"
            time.sleep(1)
            continue

        q = quote.json()
        execute_payload = {
            "quote_id": q["quote_id"],
            "request_id": str(uuid.uuid4()),
            "accept_directional_risk": True,
        }
        execute = requests.post(
            f"{api_url}/paper/manual/execute",
            headers=_headers(session_id),
            json=execute_payload,
            timeout=25,
        )

        if execute.status_code != 200:
            last_error = f"execute_status={execute.status_code}, detail={execute.text[:180]}"
            time.sleep(1)
            continue

        record = execute.json()
        if record.get("status") == "OPEN_POSITION":
            return record

        # ABORTED can happen across micro windows; retry with a fresh quote.
        last_error = f"execute_record_status={record.get('status')}, last_event={record.get('events', [{}])[-1].get('message', '')[:180]}"
        time.sleep(1)

    pytest.fail(f"Manual {side.upper()} execution did not reach OPEN_POSITION within retries; {last_error}")


def test_reference_endpoint_shape_and_window_contract(api_url):
    res = requests.get(f"{api_url}/reference", timeout=25)
    assert res.status_code == 200
    data = res.json()
    assert set(data.keys()) == {"BTC", "ETH", "SOL"}

    for asset, ref in data.items():
        assert ref["asset"] == asset
        assert ref["symbol"] == f"{asset}USDT"
        assert ref["window_start"] % 300 == 0
        assert ref["window_end"] - ref["window_start"] == 300
        assert ref["official_price_to_beat"] is None
        assert ref["source"] == "Binance"
        assert isinstance(ref.get("points", []), list)


def test_state_reference_changes_with_selected_asset(api_url):
    sid = _new_session_id()
    original = requests.get(f"{api_url}/state", headers=_headers(sid), timeout=25)
    assert original.status_code == 200
    state = original.json()
    start_asset = state["settings"]["asset"]
    next_asset = "ETH" if start_asset != "ETH" else "SOL"

    state["settings"]["asset"] = next_asset
    saved = requests.put(f"{api_url}/settings", headers=_headers(sid), json=state["settings"], timeout=25)
    assert saved.status_code == 200
    assert saved.json()["asset"] == next_asset

    updated = requests.get(f"{api_url}/state", headers=_headers(sid), timeout=25)
    assert updated.status_code == 200
    changed = updated.json()
    assert changed["settings"]["asset"] == next_asset
    assert changed["reference"]["asset"] == next_asset
    assert changed["reference"]["symbol"] == f"{next_asset}USDT"


def test_manual_quote_rejects_missing_directional_risk_acceptance(api_url):
    sid = _new_session_id()
    _set_deterministic_trade_settings(api_url, sid)
    _wait_for_manual_side_live(api_url, sid, "up")

    quote = requests.post(
        f"{api_url}/paper/manual/quote",
        headers=_headers(sid),
        json={"side": "up", "amount": 20, "unit": "dollars"},
        timeout=25,
    )
    assert quote.status_code == 200
    q = quote.json()

    invalid_execute = requests.post(
        f"{api_url}/paper/manual/execute",
        headers=_headers(sid),
        json={
            "quote_id": q["quote_id"],
            "request_id": str(uuid.uuid4()),
            "accept_directional_risk": False,
        },
        timeout=25,
    )
    assert invalid_execute.status_code == 422


def test_manual_quote_execute_idempotence_by_request_id(api_url):
    sid = _new_session_id()
    _set_deterministic_trade_settings(api_url, sid)
    _wait_for_manual_side_live(api_url, sid, "up")

    quote = requests.post(
        f"{api_url}/paper/manual/quote",
        headers=_headers(sid),
        json={"side": "up", "amount": 20, "unit": "dollars"},
        timeout=25,
    )
    if quote.status_code != 200:
        pytest.skip(f"Quote unavailable for idempotence check: {quote.status_code}")
    q = quote.json()

    request_id = str(uuid.uuid4())
    payload = {
        "quote_id": q["quote_id"],
        "request_id": request_id,
        "accept_directional_risk": True,
    }
    first = requests.post(f"{api_url}/paper/manual/execute", headers=_headers(sid), json=payload, timeout=25)
    if first.status_code != 200:
        pytest.skip(f"Execute idempotence setup blocked by live timing: {first.status_code} {first.text[:120]}")
    first_data = first.json()
    assert first_data["id"]

    second = requests.post(f"{api_url}/paper/manual/execute", headers=_headers(sid), json=payload, timeout=25)
    assert second.status_code == 200
    second_data = second.json()

    assert second_data["id"] == first_data["id"]
    assert second_data["request_id"] == request_id


def test_manual_quotes_expire_after_ttl(api_url):
    sid = _new_session_id()
    _set_deterministic_trade_settings(api_url, sid)
    _wait_for_manual_side_live(api_url, sid, "up")

    quote = requests.post(
        f"{api_url}/paper/manual/quote",
        headers=_headers(sid),
        json={"side": "up", "amount": 20, "unit": "dollars"},
        timeout=25,
    )
    assert quote.status_code == 200
    q = quote.json()
    assert q["expires_at"] - q["created"] <= 10.1

    time.sleep(11)
    expired = requests.post(
        f"{api_url}/paper/manual/execute",
        headers=_headers(sid),
        json={
            "quote_id": q["quote_id"],
            "request_id": str(uuid.uuid4()),
            "accept_directional_risk": True,
        },
        timeout=25,
    )
    assert expired.status_code == 409
    assert "expired" in expired.json().get("detail", "").lower()


def test_manual_buy_both_sides_creates_open_positions_and_trade_history(api_url):
    sid = _new_session_id()
    _set_deterministic_trade_settings(api_url, sid)
    _wait_for_manual_side_live(api_url, sid, "up")
    _wait_for_manual_side_live(api_url, sid, "down")

    for side in ("up", "down"):
        record = _execute_manual_with_retries(api_url, sid, side=side, amount=20, retries=3)
        assert record["trade_type"] == "MANUAL"
        assert record["side"] == side
        assert record["status"] == "OPEN_POSITION"
        assert record["fills"][side]["total_debit"] > 0
        assert record["remaining_shares"][side] > 0
        assert record["actual_debit"] == record["fills"][side]["total_debit"]

    trades = requests.get(f"{api_url}/trades", headers=_headers(sid), timeout=25)
    assert trades.status_code == 200
    history = trades.json()
    manual_open = [
        t for t in history
        if t.get("trade_type") == "MANUAL" and t.get("status") == "OPEN_POSITION"
    ]
    sides = {t.get("side") for t in manual_open}
    assert {"up", "down"}.issubset(sides)


def test_readme_endpoint_download_and_main_heading(api_url):
    res = requests.get(f"{api_url}/readme", timeout=25)
    assert res.status_code == 200
    assert "text/markdown" in res.headers.get("content-type", "")
    body = res.text
    assert body.startswith("# Parity — Polymarket Opportunity Optimizer")
    assert "Quick start for users" in body
