"""API-adapter integration tests — entry authentication.

These exercise src/api/server.py over real HTTP (FastAPI TestClient), NOT the
graph directly. They cover the retrofit entry-auth: an unauthenticated caller
must be rejected with 401 (before the graph runs), and an authenticated caller
must reach the graph. The server's default agent builds real LLM/KB clients
lazily (raising when secrets are absent), so the success-path test swaps in a
graph wired with fakes — the point under test is the AUTH gate, not the LLM.

This agent has NO tenant/org scope model (product_kb/proposal_kb is a
per-deployment shared catalog — see src/services/kb_search_service.py), so only
the entry-adapter auth core is wired; there are no scope-default env vars.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from framework.schemas.agent_status import AgentStatus

from src.graph.graph import Graph
from src.services.kb_search_service import KBSearchService

from tests.conftest import FakeLLM, FakeVectorStore, product_hit, proposal_hit

_TOKEN = "test-invoke-token-abc123"
_NOTES = "商談メモ: 棚卸に月3人日かかっており、担当者しか在庫状況が分からない。"
_PAYLOAD = {"input": _NOTES, "session_id": "sess-owner"}


def _fake_agent():
    kb = KBSearchService(
        FakeVectorStore([product_hit()]),
        FakeVectorStore([proposal_hit()]),
    )
    agent = Graph(config={"llm": FakeLLM(), "kb_service": kb})
    agent.compile()
    return agent


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", _TOKEN)
    import src.api.server as server

    importlib.reload(server)
    # Swap the module-level agent for one wired with fakes (no secrets/network),
    # so an authenticated call can reach a working graph.
    server.agent = _fake_agent()
    return TestClient(server.app), server


def _auth(token: str = _TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_open(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_runtime_config_is_loaded_from_config_yaml(client):
    _, server = client
    config = server._load_runtime_config()
    assert config["max_retry"] == 3
    assert config["top_k"] == 5
    assert config["vector_store"]["enabled"] is False
    assert config["vector_store"]["url"] == "http://qdrant:6333"
    assert config["vector_store"]["product_collection_name"] == "product_kb"
    assert config["vector_store"]["proposal_collection_name"] == "proposal_kb"


def test_invoke_without_token_is_401(client):
    c, _ = client
    r = c.post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_wrong_token_is_401(client):
    c, _ = client
    r = c.post("/invoke", json=_PAYLOAD, headers=_auth("wrong-token"))
    assert r.status_code == 401


def test_invoke_unauthenticated_when_token_unset_is_401(monkeypatch):
    """With INVOKE_AUTH_TOKEN unset, an unauthenticated caller (no verified
    middleware trust) must get 401 — never the old ANONYMOUS-passthrough."""
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    import src.api.server as server

    importlib.reload(server)
    r = TestClient(server.app).post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_explicit_anonymous_middleware_is_401(monkeypatch):
    """A middleware that explicitly stamps ANONYMOUS must be treated as
    unauthenticated (401), not accepted because request.state.trust_level is
    set. Test the trust VALUE, not attribute presence."""
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    from framework.schemas.trust_level import TrustLevel

    import src.api.server as server

    importlib.reload(server)

    @server.app.middleware("http")
    async def _stamp_anonymous(request, call_next):
        request.state.trust_level = TrustLevel.ANONYMOUS
        return await call_next(request)

    r = TestClient(server.app).post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_string_anonymous_middleware_is_401(monkeypatch):
    """entry-adapter v0.2.0: middleware may stamp the raw string "ANONYMOUS".
    _coerce_trust must normalize it so the unauthenticated guard still fires."""
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    import src.api.server as server

    importlib.reload(server)

    @server.app.middleware("http")
    async def _stamp_str_anonymous(request, call_next):
        request.state.trust_level = "ANONYMOUS"
        return await call_next(request)

    r = TestClient(server.app).post("/invoke", json=_PAYLOAD)
    assert r.status_code == 401


def test_invoke_with_valid_token_reaches_graph_and_succeeds(client):
    """Authenticated caller passes the gate and reaches the (fake-wired) graph,
    producing a successful proposal draft."""
    c, _ = client
    r = c.post("/invoke", json=_PAYLOAD, headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == AgentStatus.SUCCESS.value
