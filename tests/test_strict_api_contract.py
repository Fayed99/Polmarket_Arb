import json
import time
import uuid

import pytest
import requests


# Strict strategy API contract checks on public deployment
def _headers(session_id: str) -> dict:
    return {"X-Session-ID": session_id, "Content-Type": "application/json"}


def test_state_and_sse_publish_strict_strategy_only(api_url):
    sid = str(uuid.uuid4())
    state = requests.get(f"{api_url}/state", headers=_headers(sid), timeout=30)
    assert state.status_code == 200
    payload = state.json()

    assert payload["policy"]["strategy"] == "strict_arbitrage"
    assert payload["policy"]["profit_floor"] == "0.10"
    assert payload["policy"]["safety_margin"] == "0.02"
    assert payload["policy"]["required_profit"] == "0.12"
    assert payload["policy"]["minimum_total_cost"] == "1.00"
    assert payload["decision"].get("strategy") == "strict_arbitrage"

    with requests.get(
        f"{api_url}/stream", params={"session_id": sid}, stream=True, timeout=(10, 20)
    ) as stream:
        assert stream.status_code == 200
        first_data = None
        for raw in stream.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data: "):
                first_data = json.loads(raw[6:])
                break
    assert first_data is not None
    assert first_data["decision"].get("strategy") == "strict_arbitrage"


def test_settings_reject_legacy_objective_and_min_profit_overrides(api_url):
    sid = str(uuid.uuid4())
    state = requests.get(f"{api_url}/state", headers=_headers(sid), timeout=30)
    assert state.status_code == 200
    settings = state.json()["settings"]

    settings["objective"] = "maximum_upside"
    settings["min_profit"] = 0.2
    res = requests.put(f"{api_url}/settings", headers=_headers(sid), json=settings, timeout=30)
    assert res.status_code == 422


def test_paper_quote_rejects_non_current_or_fabricated_decision_id(api_url):
    sid = str(uuid.uuid4())
    requests.get(f"{api_url}/state", headers=_headers(sid), timeout=30)

    fake = requests.post(
        f"{api_url}/paper/quote",
        headers=_headers(sid),
        json={"decision_id": "deadbeefdeadbeef"},
        timeout=30,
    )
    assert fake.status_code == 409
    assert "fresh decision" in fake.json().get("detail", "").lower() or "changed" in fake.json().get("detail", "").lower()


def test_strict_quote_document_is_single_use_when_execute_succeeds(api_url):
    sid = str(uuid.uuid4())
    deadline = time.time() + 60
    decision_id = None

    while time.time() < deadline:
        state = requests.get(f"{api_url}/state", headers=_headers(sid), timeout=30)
        assert state.status_code == 200
        decision = state.json().get("decision", {})
        if decision.get("status") == "READY" and decision.get("decision_id"):
            decision_id = decision["decision_id"]
            break
        time.sleep(1)

    if not decision_id:
        pytest.skip("No live READY strict decision observed within timeout")

    quote = requests.post(
        f"{api_url}/paper/quote",
        headers=_headers(sid),
        json={"decision_id": decision_id},
        timeout=30,
    )
    if quote.status_code != 200:
        pytest.skip(f"Quote could not be issued in live window: {quote.status_code}")

    quote_id = quote.json()["quote_id"]
    first_execute = requests.post(
        f"{api_url}/paper/execute",
        headers=_headers(sid),
        json={"quote_id": quote_id, "request_id": str(uuid.uuid4()), "scenario": "normal"},
        timeout=30,
    )
    if first_execute.status_code != 200:
        pytest.skip(f"Execute blocked by live preflight timing: {first_execute.status_code}")

    replay = requests.post(
        f"{api_url}/paper/execute",
        headers=_headers(sid),
        json={"quote_id": quote_id, "request_id": str(uuid.uuid4()), "scenario": "normal"},
        timeout=30,
    )
    assert replay.status_code == 409
