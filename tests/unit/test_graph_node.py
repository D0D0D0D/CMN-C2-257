"""SalesProposalDraftGraphNode: GraphNode contract."""

from __future__ import annotations

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.sales_proposal_graph_node import SalesProposalDraftGraphNode


class TestGraphNodeContract:
    def test_error_strategy_and_hitl_declared(self):
        assert SalesProposalDraftGraphNode.error_strategy == "propagate"
        assert SalesProposalDraftGraphNode.propagate_hitl is False
        assert SalesProposalDraftGraphNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL

    def test_upstream_error_passthrough_skips_subgraph(self):
        node = SalesProposalDraftGraphNode()
        assert node.execute({"status": AgentStatus.ERROR.value}) == {}

    def test_missing_issues_guard(self):
        node = SalesProposalDraftGraphNode()
        out = node.execute({"user_input": "x"})
        assert out["status"] == AgentStatus.ERROR

    def test_extract_input_serializes_contract_payload(self):
        node = SalesProposalDraftGraphNode()
        payload = json.loads(
            node.extract_input(
                {
                    "validated_input": "メモ",
                    "customer_issues": [{"issue": "a"}],
                    "proposal_format": {"tone": "casual"},
                }
            )
        )
        assert payload == {
            "validated_input": "メモ",
            "customer_issues": [{"issue": "a"}],
            "proposal_format": {"tone": "casual"},
        }

    def test_merge_output_maps_changed_keys_only(self):
        node = SalesProposalDraftGraphNode()
        sub_result = {
            "proposal_draft": "# d",
            "proposal_outline": {"title": "t", "sections": []},
            "matched_solutions": [1],
            "matched_stories": [2],
            "unmatched_issues": [3],
            "kb_rejected_chunks": [4],
            "compliance_flags": [5],
            "status": AgentStatus.SUCCESS.value,
            "node_history": ["should-not-merge"],
        }
        merged = node.merge_output({}, sub_result)
        assert merged["proposal_draft"] == "# d"
        assert "node_history" not in merged  # reducer field must never be returned

    def test_get_subgraph_is_cached(self):
        node = SalesProposalDraftGraphNode(config={})
        assert node.get_subgraph() is node.get_subgraph()
