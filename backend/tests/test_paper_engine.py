import asyncio
import copy
import time
import uuid

import pytest
from fastapi import HTTPException

from models import ExecutionRequest, Settings
from paper import PaperEngine


# Paper execution behavior with deterministic fixture feed and storage
class FakeCollection:
    def __init__(self):
        self.docs = []

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            ok = all(doc.get(k) == v for k, v in query.items())
            if ok:
                if projection:
                    include = {k for k, v in projection.items() if v == 1}
                    exclude = {k for k, v in projection.items() if v == 0}
                    if include:
                        return {k: doc[k] for k in include if k in doc}
                    if exclude:
                        return {k: v for k, v in doc.items() if k not in exclude}
                return copy.deepcopy(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        for i, doc in enumerate(self.docs):
            if all(doc.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    self.docs[i].update(copy.deepcopy(update["$set"]))
                return
        if upsert and "$set" in update:
            self.docs.append(copy.deepcopy(update["$set"]))


class FakeDB:
    def __init__(self):
        self.trades = FakeCollection()
        self.sessions = FakeCollection()


class FakeFeed:
    def __init__(self, market):
        self._market = market
        self.notify_count = 0

    def snapshot(self, asset):
        return copy.deepcopy(self._market)

    async def notify(self):
        self.notify_count += 1


@pytest.fixture
def market_fixture():
    now_ms = time.time() * 1000
    return {
        "id": "mkt-1",
        "slug": "btc-updown-5m-fixture",
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
                "asks": [(0.45, 20.0), (0.46, 20.0)],
                "bids": [(0.44, 20.0), (0.43, 20.0)],
            },
            "down": {
                "ready": True,
                "event_ms": now_ms,
                "arrival_ms": now_ms,
                "minimum": 5.0,
                "size_precision": 2,
                "asks": [(0.44, 20.0), (0.45, 20.0)],
                "bids": [(0.43, 20.0), (0.42, 20.0)],
            },
        },
    }


def make_quote(settings):
    return {
        "created": time.time(),
        "market_id": "mkt-1",
        "settings": settings.model_dump(),
        "decision": {
            "up": {"shares": 5.0, "total_debit": 2.53662, "highest_ask": 0.45},
            "down": {"shares": 5.0, "total_debit": 2.48278, "highest_ask": 0.44},
            "deployed": 5.0194,
            "budget": settings.budget,
            "status": "READY",
        },
    }


def make_session(settings, cash=1000):
    return {
        "id": str(uuid.uuid4()),
        "settings": settings,
        "cash": cash,
        "quotes": {"q1": make_quote(settings)},
        "lock": asyncio.Lock(),
    }


@pytest.mark.anyio
async def test_execute_both_fills_locked_position(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0)
    session = make_session(settings, cash=1000)
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    monkeypatch.setattr("random.random", lambda: 0.99)

    record = await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-12345678", scenario="normal"))
    assert record["status"] == "LOCKED_POSITION"
    assert set(record["fills"].keys()) == {"up", "down"}
    assert session["cash"] < 1000


@pytest.mark.anyio
async def test_preflight_abort_when_stale_scenario(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0)
    session = make_session(settings, cash=1000)
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    record = await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-22345678", scenario="stale"))
    assert record["status"] == "ABORTED"
    assert record["actual_debit"] == 0


@pytest.mark.anyio
async def test_one_sided_flow_results_in_unwind_or_recovery_required(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0, max_recovery_loss=2)
    session = make_session(settings, cash=1000)
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    monkeypatch.setattr("random.random", lambda: 0.99)
    record = await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-32345678", scenario="one_sided"))
    assert record["status"] in {"UNWOUND", "RECOVERY_REQUIRED"}
    assert len(record["fills"]) == 1


@pytest.mark.anyio
async def test_execute_idempotent_by_request_id(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0)
    session = make_session(settings, cash=1000)
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    monkeypatch.setattr("random.random", lambda: 0.99)

    first = await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-42345678", scenario="normal"))
    cash_after_first = session["cash"]
    second = await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-42345678", scenario="normal"))
    assert second["id"] == first["id"]
    assert session["cash"] == cash_after_first


@pytest.mark.anyio
async def test_expired_quote_rejected(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0)
    session = make_session(settings, cash=1000)
    session["quotes"]["q1"]["created"] = time.time() - 15
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)

    with pytest.raises(HTTPException) as exc:
        await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-52345678", scenario="normal"))
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_insufficient_cash_aborts_pre_submit(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0)
    session = make_session(settings, cash=1)
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    monkeypatch.setattr("random.random", lambda: 0.99)

    record = await engine.execute(session, ExecutionRequest(quote_id="q1", request_id="rid-62345678", scenario="normal"))
    assert record["status"] == "ABORTED"
    assert record["actual_debit"] == 0


@pytest.mark.anyio
async def test_request_id_isolated_across_sessions(monkeypatch, market_fixture):
    settings = Settings(asset="BTC", budget=100, max_loss=1, latency_ms=0, failure_rate=0)
    db = FakeDB()
    engine = PaperEngine(FakeFeed(market_fixture), db)
    monkeypatch.setattr("random.uniform", lambda a, b: 0)
    monkeypatch.setattr("random.random", lambda: 0.99)

    s1 = make_session(settings, cash=1000)
    s2 = make_session(settings, cash=1000)
    req_id = "rid-72345678"
    r1 = await engine.execute(s1, ExecutionRequest(quote_id="q1", request_id=req_id, scenario="normal"))
    r2 = await engine.execute(s2, ExecutionRequest(quote_id="q1", request_id=req_id, scenario="normal"))
    assert r1["id"] != r2["id"]
