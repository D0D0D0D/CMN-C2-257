"""AgentCore Platform v1.0"""

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.kb_search_service import build_default_kb_service

_MAX_HITS_PER_ISSUE = 3


class ProductMatchNode(FunctionNode):
    """Inner-graph entry node: JSON payload restore + shared-RAG search.

    1. Deserializes the JSON payload the outer extract_input() packed into
       user_input (inner graphs receive user_input(str) + ctx only) and
       expands customer_issues / proposal_format into inner State.
    2. Searches product_kb / proposal_kb per issue via KBSearchService.
       Contract-violating hits (Data Contract 1.0.0 §6 metadata missing)
       are rejected; zero-hit issues are quarantined in unmatched_issues —
       never guessed (hallucination guard).
    """

    # Touches the shared sales RAG — verified callers only.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, kb_service=None, config: dict | None = None):
        super().__init__()
        self._kb_service = kb_service
        self._config = dict(config or {})

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}  # upstream error passthrough

        try:
            payload = json.loads(state.get("user_input", "") or "{}")
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            emit_trace_event("kb_search_failed", {"reason": "invalid_payload"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ProductMatchNode: invalid inner payload JSON — {e}"],
            }

        issues = [
            i for i in payload.get("customer_issues", [])
            if isinstance(i, dict) and str(i.get("issue", "")).strip()
        ]
        if not issues:
            emit_trace_event("kb_search_failed", {"reason": "no_issues"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ProductMatchNode: payload contains no customer_issues"],
            }
        proposal_format = payload.get("proposal_format")
        if not isinstance(proposal_format, dict):
            proposal_format = None

        kb = self._kb_service or build_default_kb_service(self._config)

        matched_solutions: list = []
        matched_stories: list = []
        unmatched_issues: list = []
        kb_rejected_chunks: list = []
        for issue in issues:
            found = kb.search_issue(str(issue["issue"]))
            kb_rejected_chunks.extend(found["rejected"])
            solutions = found["solutions"][:_MAX_HITS_PER_ISSUE]
            stories = found["stories"][:_MAX_HITS_PER_ISSUE]
            if not solutions and not stories:
                unmatched_issues.append(
                    {**issue, "reason": "RAG ヒット 0 件 — 該当情報なし・要確認（推測生成しない）"}
                )
                continue
            matched_solutions.extend({**hit, "issue": issue["issue"]} for hit in solutions)
            matched_stories.extend({**hit, "issue": issue["issue"]} for hit in stories)

        emit_trace_event(
            "kb_search_completed",
            {
                "issue_count": len(issues),
                "solution_hits": len(matched_solutions),
                "story_hits": len(matched_stories),
                "unmatched_issues": len(unmatched_issues),
                "rejected_chunks": len(kb_rejected_chunks),
            },
            state,
        )
        return {
            "customer_issues": issues,
            "proposal_format": proposal_format,
            "validated_input": str(payload.get("validated_input", "")),
            "matched_solutions": matched_solutions,
            "matched_stories": matched_stories,
            "unmatched_issues": unmatched_issues,
            "kb_rejected_chunks": kb_rejected_chunks,
            "status": AgentStatus.SUCCESS.value,
        }
