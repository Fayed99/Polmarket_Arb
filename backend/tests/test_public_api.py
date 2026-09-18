import json
import time
import uuid

import requests


# Public API contract and transport behavior
def test_health_endpoint(api_url):
    res = requests.get(f"{api_url}/health", timeout=20)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["mode"] == "PAPER"
    assert data["live_execution"] is False
    assert isinstance(data["markets"], dict)


def test_state_creates_session_and_returns_shape(api_url):
    sid = str(uuid.uuid4())
    res = requests.get(f"{api_url}/state", headers={"X-Session-ID": sid}, timeout=30)
    assert res.status_code == 200
    data = res.json()
    assert data["settings"]["budget"] >= 0
    assert data["wallet"]["mode"] == "PAPER"
    assert data["wallet"]["cash"] >= 0
    assert data["decision"]["budget"] == data["settings"]["budget"]
    assert "diagnostics" in data and "status" in data["diagnostics"]


def test_state_rejects_invalid_session_id(api_url):
    res = requests.get(f"{api_url}/state", headers={"X-Session-ID": "not-a-uuid"}, timeout=20)
    assert res.status_code == 400
    detail = res.json().get("detail", "")
    assert "session" in detail.lower()


def test_stream_continuous_with_one_second_fallback(api_url):
    sid = str(uuid.uuid4())
    times, seen = [], []
    with requests.get(
        f"{api_url}/stream",
        params={"session_id": sid},
        stream=True,
        timeout=(10, 20),
    ) as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers.get("content-type", "")
        for raw in res.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            payload = json.loads(raw[6:])
            seen.append(payload)
            times.append(time.monotonic())
            if len(seen) >= 3:
                break
    assert len(seen) >= 2
    assert all("decision" in item and "diagnostics" in item for item in seen)
    gaps = [times[i] - times[i - 1] for i in range(1, len(times))]
    assert max(gaps) <= 1.5


def test_live_execute_is_locked(api_url):
    res = requests.post(f"{api_url}/live/execute", timeout=20)
    assert res.status_code == 423
    detail = res.json().get("detail", "")
    assert "locked" in detail.lower()
