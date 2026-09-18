import asyncio
import copy
import time
import uuid

import pytest
from fastapi import HTTPException

from models import Settings
from quote_store import QuoteStore


# Durable quote storage behavior and single-use claiming
class FakeCollection:
    def __init__(self):
        self.docs = []

    @staticmethod
    def _matches(doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict):
                if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _project(doc, projection):
        if not projection:
            return copy.deepcopy(doc)
        include = {k for k, v in projection.items() if v == 1}
        exclude = {k for k, v in projection.items() if v == 0}
        if include:
            return {k: copy.deepcopy(doc[k]) for k in include if k in doc}
        if exclude:
            return {k: copy.deepcopy(v) for k, v in doc.items() if k not in exclude}
        return copy.deepcopy(doc)

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if self._matches(doc, query):
                return self._project(doc, projection)
        return None

    async def find_one_and_update(self, query, update, projection=None, return_document=None):
        for i, doc in enumerate(self.docs):
            if self._matches(doc, query):
                if "$set" in update:
                    self.docs[i].update(copy.deepcopy(update["$set"]))
                return self._project(self.docs[i], projection)
        return None

    async def update_one(self, query, update, upsert=False):
        for i, doc in enumerate(self.docs):
            if self._matches(doc, query):
                if "$set" in update:
                    self.docs[i].update(copy.deepcopy(update["$set"]))
                return

    async def update_many(self, query, update):
        for i, doc in enumerate(self.docs):
            if self._matches(doc, query) and "$set" in update:
                self.docs[i].update(copy.deepcopy(update["$set"]))


class FakeDB:
    def __init__(self):
        self.quotes = FakeCollection()


@pytest.fixture
def market_fixture():
    return {
        "id": "mkt-1",
        "condition_id": "condition-1",
        "start": time.time() - 60,
        "end": time.time() + 120,
        "fee": {"r": 0.07, "e": 1, "to": "c"},
        "books": {
            "up": {"token_id": "up-token", "tick": "0.01", "minimum": 5.0, "size_precision": 2},
            "down": {"token_id": "down-token", "tick": "0.01", "minimum": 5.0, "size_precision": 2},
        },
    }


def _session(budget=100):
    return {
        "id": str(uuid.uuid4()),
        "settings": Settings(budget=budget),
        "cash": budget,
        "lock": asyncio.Lock(),
    }


@pytest.mark.anyio
async def test_quote_claim_is_single_use_and_second_claim_rejected(market_fixture):
    db = FakeDB()
    store = QuoteStore(db)
    session = _session()
    quote = await store.issue(session, market_fixture, "strict_arbitrage", {"decision": {"q": "5"}})

    loaded = await store.load(session, quote["quote_id"], "strict_arbitrage")
    await store.claim(session, loaded, "rid-1", "exec-1")

    with pytest.raises(HTTPException) as exc:
        await store.claim(session, loaded, "rid-2", "exec-2")
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_quote_workspace_and_kind_binding_enforced(market_fixture):
    db = FakeDB()
    store = QuoteStore(db)
    s1 = _session()
    s2 = _session()
    quote = await store.issue(s1, market_fixture, "strict_arbitrage", {"decision": {"q": "5"}})

    with pytest.raises(HTTPException) as exc_workspace:
        await store.load(s2, quote["quote_id"], "strict_arbitrage")
    assert exc_workspace.value.status_code == 409

    with pytest.raises(HTTPException) as exc_kind:
        await store.load(s1, quote["quote_id"], "manual_directional")
    assert exc_kind.value.status_code == 409


@pytest.mark.anyio
async def test_quote_rejected_on_settings_hash_change_or_expiry(market_fixture):
    db = FakeDB()
    store = QuoteStore(db)
    session = _session(budget=100)
    quote = await store.issue(session, market_fixture, "strict_arbitrage", {"decision": {"q": "5"}})

    session["settings"] = Settings(budget=101)
    with pytest.raises(HTTPException) as exc_hash:
        await store.load(session, quote["quote_id"], "strict_arbitrage")
    assert exc_hash.value.status_code == 409

    session["settings"] = Settings(budget=100)
    for doc in db.quotes.docs:
        if doc.get("quote_id") == quote["quote_id"]:
            doc["expires_at"] = time.time() - 1
            break
    with pytest.raises(HTTPException) as exc_expiry:
        await store.load(session, quote["quote_id"], "strict_arbitrage")
    assert exc_expiry.value.status_code == 409


@pytest.mark.anyio
async def test_store_restart_loads_ready_quote_but_consumed_cannot_replay(market_fixture):
    db = FakeDB()
    store_a = QuoteStore(db)
    session = _session()
    quote = await store_a.issue(session, market_fixture, "strict_arbitrage", {"decision": {"q": "5"}})

    store_b = QuoteStore(db)
    loaded = await store_b.load(session, quote["quote_id"], "strict_arbitrage")
    assert loaded["state"] == "READY"

    await store_b.claim(session, loaded, "rid-1", "exec-1")
    await store_b.finish(session, quote["quote_id"], "ABORTED")

    with pytest.raises(HTTPException) as exc:
        await store_b.load(session, quote["quote_id"], "strict_arbitrage")
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_concurrent_claim_only_one_wins(market_fixture):
    db = FakeDB()
    store = QuoteStore(db)
    session = _session()
    quote = await store.issue(session, market_fixture, "strict_arbitrage", {"decision": {"q": "5"}})
    loaded = await store.load(session, quote["quote_id"], "strict_arbitrage")

    async def claim_once(request_id):
        try:
            await store.claim(session, loaded, request_id, f"exec-{request_id}")
            return True
        except HTTPException:
            return False

    won = await asyncio.gather(claim_once("rid-a"), claim_once("rid-b"))
    assert sum(1 for x in won if x) == 1
