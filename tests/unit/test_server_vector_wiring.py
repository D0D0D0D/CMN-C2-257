"""Startup policy and lifecycle tests for the optional SDK Qdrant sales KB."""

from __future__ import annotations

import asyncio

import pytest

from src.services.kb_search_service import build_default_kb_service, set_default_kb_service


class _Secrets:
    def __init__(self, value: str = "test-embedding-key") -> None:
        self.value = value

    def require(self, key: str) -> str:
        assert key == "SALES_KB_EMBEDDING_API_KEY"
        return self.value


def _valid_config():
    return {
        "top_k": 5,
        "min_score": 0.25,
        "vector_store": {
            "enabled": True,
            "url": "http://qdrant:6333",
            "product_collection_name": "product_kb",
            "proposal_collection_name": "proposal_kb",
            "namespace": "cmn",
            "agent_name": "cmn_c2_257",
            "vector_size": 1536,
            "distance": "cosine",
            "timeout_s": 30,
            "write_timeout_s": 60,
            "max_batch_documents": 1000,
            "max_top_k": 100,
            "embedding_base_url": "https://embedding.example/v1",
            "embedding_model": "text-embedding-3-small",
            "embedding_api_version": "2024-02-15-preview",
            "embedding_provider": "openai",
        },
    }


def test_store_absent_or_disabled_preserves_zero_hit_degradation():
    import src.api.server as server

    for config in ({}, {"vector_store": {"enabled": False}}):
        set_default_kb_service(object())  # prove wiring clears a previous service
        asyncio.run(server._wire_dependencies(config=config, secret_provider=_Secrets()))
        assert build_default_kb_service().search_issue("在庫管理") == {
            "solutions": [],
            "stories": [],
            "rejected": [],
        }


def test_enabled_store_with_missing_config_fails_startup():
    import src.api.server as server

    with pytest.raises(ValueError, match="missing required keys"):
        asyncio.run(
            server._wire_dependencies(
                config={"vector_store": {"enabled": True, "url": "http://qdrant:6333"}},
                secret_provider=_Secrets(),
            )
        )


def test_enabled_store_with_missing_embedding_secret_fails_startup():
    import src.api.server as server

    class _MissingSecrets:
        def require(self, key: str) -> str:
            raise KeyError(key)

    with pytest.raises(KeyError, match="SALES_KB_EMBEDDING_API_KEY"):
        asyncio.run(server._wire_dependencies(config=_valid_config(), secret_provider=_MissingSecrets()))


def test_enabled_store_uses_embedding_secret_and_runtime_lifecycle(monkeypatch):
    import src.api.server as server

    constructed: list[dict] = []

    class _Runtime:
        closed = False

        async def aclose(self, *, timeout=None):
            assert timeout == 5.0
            self.closed = True

    class _Qdrant:
        def __init__(self, config, *, runtime):
            constructed.append(config)
            self.runtime = runtime

        def search(self, query, top_k=10):
            return []

    runtime = _Runtime()
    monkeypatch.setattr(server, "AsyncServiceRuntime", lambda: runtime)
    monkeypatch.setattr(server, "QdrantVectorStore", _Qdrant)

    asyncio.run(server._wire_dependencies(config=_valid_config(), secret_provider=_Secrets()))

    assert [c["collection_name"] for c in constructed] == ["product_kb", "proposal_kb"]
    assert all(c["embedding_api_key"] == "test-embedding-key" for c in constructed)
    assert build_default_kb_service().search_issue("在庫管理") == {
        "solutions": [],
        "stories": [],
        "rejected": [],
    }

    asyncio.run(server._shutdown_dependencies())
    assert runtime.closed is True


def test_qdrant_constructor_failure_is_not_silently_degraded(monkeypatch):
    import src.api.server as server

    class _Runtime:
        closed = False

        async def aclose(self, *, timeout=None):
            self.closed = True

    class _InvalidQdrant:
        def __init__(self, config, *, runtime):
            raise ValueError("invalid Qdrant configuration")

    runtime = _Runtime()
    monkeypatch.setattr(server, "AsyncServiceRuntime", lambda: runtime)
    monkeypatch.setattr(server, "QdrantVectorStore", _InvalidQdrant)

    with pytest.raises(ValueError, match="invalid Qdrant configuration"):
        asyncio.run(server._wire_dependencies(config=_valid_config(), secret_provider=_Secrets()))
    assert runtime.closed is True
