"""AgentCore Platform v1.0"""

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel


class SalesProposalDraftGraphNode(GraphNode):
    """`main` slot GraphNode wrapping the inner proposal-draft workflow.

    Contract (docs/02_design.md):
    - extract_input(): JSON-serialize validated_input + customer_issues +
      proposal_format (inner graphs receive user_input(str) + ctx only).
    - merge_output(): map inner get_output() fields back — changed keys only.
    - get_subgraph(): cached — never rebuild per execute().
    - Upstream status=="error" passes through without starting the subgraph
      (pre→main is an unconditional edge).
    """

    # Fail fast: inner LLM/RAG failures surface as SubgraphError.
    error_strategy: ClassVar[str] = "propagate"
    # hitl.enabled: false — interrupts never propagate (no HITL in this AG).
    propagate_hitl: ClassVar[bool] = False

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None):
        super().__init__()
        self._config = dict(config or {})
        self._subgraph = None

    def execute(self, state: AgentState) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}  # upstream error passthrough — do not start the subgraph
        if not state.get("customer_issues"):
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "SalesProposalDraftGraphNode: customer_issues missing — "
                    "pre_process must run first"
                ],
            }
        return super().execute(state)

    def get_subgraph(self):
        if self._subgraph is None:
            from src.graph.domain_workflow_graph import SalesProposalDraftGraph

            self._subgraph = SalesProposalDraftGraph(config=self._config)
        return self._subgraph

    def extract_input(self, state: AgentState) -> str:
        return json.dumps(
            {
                "validated_input": state.get("validated_input", state.get("user_input", "")),
                "customer_issues": state.get("customer_issues", []),
                "proposal_format": state.get("proposal_format"),
            },
            ensure_ascii=False,
            default=str,
        )

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:
        return {
            "proposal_draft": sub_result.get("proposal_draft", ""),
            "proposal_outline": sub_result.get("proposal_outline", {}),
            "matched_solutions": sub_result.get("matched_solutions", []),
            "matched_stories": sub_result.get("matched_stories", []),
            "unmatched_issues": sub_result.get("unmatched_issues", []),
            "kb_rejected_chunks": sub_result.get("kb_rejected_chunks", []),
            "compliance_flags": sub_result.get("compliance_flags", []),
            "status": sub_result.get("status", AgentStatus.SUCCESS.value),
        }
