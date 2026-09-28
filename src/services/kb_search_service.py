"""AgentCore Platform v1.0 — shared-RAG search service (CMN-C2-257)."""

# Read-only consumer of the Sales Enablement cluster shared RAG
# (product_kb / proposal_kb), using Data Contract version 1.0.0.
#
# Isolation model (NO tenant/scope key by design): product_kb / proposal_kb is a
# per-deployment SHARED CATALOG. Isolation is the deployment-specific Qdrant
# endpoint plus the SDK namespace/agent tag — there is NO
# within-customer must-not-mix partition, so search() takes no tenant/org scope
# argument; org-wide search over both collections is the intended behaviour.
# proposal_kb entries must be suitable for org-wide reuse before ingestion;
# any end-customer visibility restriction is an ingestion-side concern and is
# not enforceable here.
#
# Drift guard (path (b) — customer-provisioned RAG): hits missing any of the
# required metadata keys are NOT adopted (source citation would be
# impossible); they are reported via `rejected` for the confirmation summary.

from __future__ import annotations

from typing import Protocol

# Data Contract 1.0.0 §6 — required metadata (both collections).
CONTRACT_VERSION = "1.0.0"
REQUIRED_METADATA_KEYS = ("record_id", "chunk_hash", "source_doc", "kb_type")
TITLE_METADATA_KEYS = ("product_name", "proposal_title")


class _SearchStore(Protocol):
    """Read-only vector-search surface consumed by this agent."""

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        ...


class _EmptyVectorStore:
    """No-op store used for the documented unconfigured-KB degradation."""

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        return []


class KBSearchService:
    """Searches product_kb / proposal_kb and enforces contract compliance."""

    def __init__(
        self,
        product_store: _SearchStore,
        proposal_store: _SearchStore,
        top_k: int = 5,
        min_score: float = 0.0,
    ):
        self._product_store = product_store
        self._proposal_store = proposal_store
        self._top_k = top_k
        self._min_score = min_score

    def search_issue(self, issue_text: str) -> dict:
        """Search both collections for one customer issue.

        Returns {"solutions": [...], "stories": [...], "rejected": [...]}.
        Raises whatever the underlying store raises (connection failures
        propagate — fail fast per design).
        """
        solutions, rejected_p = self._validated(
            self._product_store.search(issue_text, top_k=self._top_k), "product"
        )
        stories, rejected_s = self._validated(
            self._proposal_store.search(issue_text, top_k=self._top_k), "proposal"
        )
        return {"solutions": solutions, "stories": stories, "rejected": rejected_p + rejected_s}

    def _validated(self, hits: list, kb_type: str) -> tuple[list, list]:
        adopted: list = []
        rejected: list = []
        for hit in hits or []:
            if not isinstance(hit, dict):
                continue
            score = hit.get("score")
            if isinstance(score, (int, float)) and score < self._min_score:
                continue
            metadata = hit.get("metadata") or {}
            missing = [k for k in REQUIRED_METADATA_KEYS if not metadata.get(k)]
            if not any(metadata.get(k) for k in TITLE_METADATA_KEYS):
                missing.append("|".join(TITLE_METADATA_KEYS))
            if missing:
                rejected.append(
                    {
                        "kb_type": kb_type,
                        "source_doc": metadata.get("source_doc", "(unknown)"),
                        "missing_metadata": missing,
                        "reason": "Data Contract 1.0.0 §6 required metadata missing — 出典不明のため不採用",
                    }
                )
                continue
            adopted.append(
                {
                    "content": hit.get("content", ""),
                    "score": score,
                    "record_id": metadata["record_id"],
                    "chunk_hash": metadata["chunk_hash"],
                    "source_doc": metadata["source_doc"],
                    "kb_type": metadata["kb_type"],
                    "product_name": metadata.get("product_name", ""),
                    "proposal_title": metadata.get("proposal_title", ""),
                }
            )
        return adopted, rejected


_default_kb_service: KBSearchService | None = None


def set_default_kb_service(service: KBSearchService | None) -> None:
    """Set the process-lifecycle-owned production service used by graph nodes."""
    global _default_kb_service
    _default_kb_service = service


def build_default_kb_service(config: dict | None = None) -> KBSearchService:
    """Return the wired Qdrant service or the documented empty-KB fallback.

    FastAPI lifespan owns the SDK runtime and wires the real service. Direct
    graph use without that wiring preserves the existing "KB 未整備" behavior:
    both searches return zero hits and the issue is sent to unmatched_issues.
    """
    if _default_kb_service is not None:
        return _default_kb_service

    cfg = dict(config or {})
    return KBSearchService(
        product_store=_EmptyVectorStore(),
        proposal_store=_EmptyVectorStore(),
        top_k=int(cfg.get("top_k", 5)),
        min_score=float(cfg.get("min_score", 0.0)),
    )
