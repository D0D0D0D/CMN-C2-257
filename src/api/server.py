"""AgentCore Platform v1.0 — CMN-C2-257 HTTP entry point.

Standalone HTTP entry point for the agent. Entry points are adapters only —
no business logic here. For platform-level routing, AgentGateway calls
agent.invoke() directly.

Entry authentication is handled by entry_adapter.auth:
  - a verified middleware trust (request.state.trust_level) is used as-is;
  - otherwise INVOKE_AUTH_TOKEN Bearer auth promotes ANONYMOUS →
    VERIFIED_EXTERNAL (_resolve_trust raises 401 on token mismatch);
  - an unauthenticated caller (no verified middleware trust AND no token
    promotion) is rejected with 401.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from shared.services.runtime import AsyncServiceRuntime
from shared.services.vector_store.qdrant_vector_store import QdrantVectorStore

from src.entry_adapter.auth import _require_verified_trust, _resolve_trust
from src.graph.graph import Graph
from src.services.kb_search_service import KBSearchService, set_default_kb_service
from src.services.sales_kb_qdrant_adapter import SalesKBQdrantAdapter

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
_VECTOR_STORE_CONFIG_KEY = "vector_store"
_EMBEDDING_API_KEY_SECRET = "SALES_KB_EMBEDDING_API_KEY"
_COLLECTION_KEYS = frozenset({"product_collection_name", "proposal_collection_name"})
_QDRANT_CONFIG_KEYS = frozenset(
    {
        "url",
        "namespace",
        "agent_name",
        "vector_size",
        "distance",
        "timeout_s",
        "write_timeout_s",
        "max_batch_documents",
        "max_top_k",
        "embedding_base_url",
        "embedding_model",
        "embedding_api_version",
        "embedding_provider",
    }
)
_service_runtime: AsyncServiceRuntime | None = None


def _load_runtime_config(path: Path = _RUNTIME_CONFIG_PATH) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise RuntimeError("config/config.yaml must contain a mapping")
    return loaded


agent = Graph(config=_load_runtime_config())
agent.compile()
_secret_provider = secrets_factory(namespace="cmn", agent_name="cmn_c2_257")
agent.provision_secrets(_secret_provider)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    runtime_config = getattr(application.state, "service_config", agent.config)
    secret_provider = getattr(application.state, "secret_provider", _secret_provider)
    await _wire_dependencies(config=runtime_config, secret_provider=secret_provider)
    try:
        yield
    finally:
        await _shutdown_dependencies()


app = FastAPI(title="Sales Proposal Draft Generation Agent", lifespan=_lifespan)


async def _wire_dependencies(*, config: Mapping[str, Any], secret_provider: Any) -> None:
    """Wire the optional SDK Qdrant stores into the existing KB service seam.

    An absent or explicitly disabled section preserves the documented empty-KB
    degradation. Once enabled, malformed config, a missing embedding secret,
    or Qdrant initialization failure aborts startup.
    """
    global _service_runtime

    await _shutdown_dependencies()
    raw_config = config.get(_VECTOR_STORE_CONFIG_KEY)
    if raw_config is None:
        return
    if not isinstance(raw_config, Mapping):
        raise ValueError(f"config.{_VECTOR_STORE_CONFIG_KEY} must be a mapping")

    enabled = raw_config.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"config.{_VECTOR_STORE_CONFIG_KEY}.enabled must be a boolean")
    if not enabled:
        return

    store_config = _validate_qdrant_config(raw_config)
    with bound_secrets(secret_provider):
        store_config["embedding_api_key"] = secret_provider.require(_EMBEDDING_API_KEY_SECRET)

    runtime = AsyncServiceRuntime()
    try:
        product_store = QdrantVectorStore(
            _for_collection(store_config, "product_collection_name"), runtime=runtime
        )
        proposal_store = QdrantVectorStore(
            _for_collection(store_config, "proposal_collection_name"), runtime=runtime
        )
        top_k = int(config.get("top_k", 5))
        if top_k <= 0 or top_k > int(store_config["max_top_k"]):
            raise ValueError("config.top_k must be positive and no greater than vector_store.max_top_k")
        min_score = float(config.get("min_score", 0.0))
    except BaseException:
        await runtime.aclose(timeout=5.0)
        raise

    set_default_kb_service(
        KBSearchService(
            product_store=SalesKBQdrantAdapter(product_store),
            proposal_store=SalesKBQdrantAdapter(proposal_store),
            top_k=top_k,
            min_score=min_score,
        )
    )
    _service_runtime = runtime


def _validate_qdrant_config(raw_config: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _QDRANT_CONFIG_KEYS | _COLLECTION_KEYS | {"enabled"}
    unknown = sorted(set(raw_config) - allowed)
    if unknown:
        raise ValueError(f"config.{_VECTOR_STORE_CONFIG_KEY} has unknown keys: {unknown}")
    required = _QDRANT_CONFIG_KEYS | _COLLECTION_KEYS
    missing = sorted(key for key in required if raw_config.get(key) in (None, ""))
    if missing:
        raise ValueError(f"config.{_VECTOR_STORE_CONFIG_KEY} is missing required keys: {missing}")
    return {key: raw_config[key] for key in required}


def _for_collection(config: Mapping[str, Any], collection_key: str) -> dict[str, Any]:
    return {
        **{key: config[key] for key in _QDRANT_CONFIG_KEYS},
        "collection_name": config[collection_key],
        "embedding_api_key": config["embedding_api_key"],
    }


async def _shutdown_dependencies() -> None:
    global _service_runtime
    runtime, _service_runtime = _service_runtime, None
    set_default_kb_service(None)
    if runtime is not None:
        await runtime.aclose(timeout=5.0)


def _ctx(request: Request, session_id: str) -> InvocationContext:
    """Build the InvocationContext AFTER entry authentication.

    Scope note: the entry_adapter part also exposes a tenant/org scope contract,
    but this template has NO scope model. The Sales-Enablement RAG it reads
    (product_kb = product catalog, proposal_kb = past proposal stories) is a
    per-deployment SHARED CATALOG governed by a Data Contract (v1.0.0); the
    isolation boundary is the deployment-specific Qdrant endpoint plus the SDK
    namespace/agent tag, NOT a per-request scope key. There is no
    within-customer must-not-mix partition — an org-wide search over both
    collections is the intended behaviour. Enforcing authenticate_scope() would
    gate on a scope nothing consumes, and a default fallback would silently
    match everything, so only the auth core (_resolve_trust /
    _require_verified_trust) is wired.
    """
    trust, token_authenticated = _resolve_trust(request)
    # Unauthenticated = trust still ANONYMOUS after resolution and no token
    # promotion. Test the trust VALUE, not attribute presence — a middleware that
    # explicitly stamps ANONYMOUS must still be treated as unauthenticated (401).
    if trust is TrustLevel.ANONYMOUS and not token_authenticated:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    _require_verified_trust(trust)
    return InvocationContext(
        session_id=session_id or str(uuid4()),
        caller_trust_level=trust,
        caller_id=getattr(request.state, "caller_id", ""),
    )


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    ctx = _ctx(request, req.session_id)
    with bound_secrets(agent._secrets_provider):
        return agent.invoke(req.input, ctx=ctx)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "cmn_c2_257"}
