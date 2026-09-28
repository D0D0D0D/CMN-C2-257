"""AgentCore Platform v1.0"""

# CMN-C2-257 — Cat 2 outer graph (fixed 5-node backbone).
# Domain complexity lives inside SalesProposalDraftGraphNode (`main` slot),
# which wraps src/graph/domain_workflow_graph.py.
# add_edges()/route() are NOT overridden — backbone wiring is framework-owned:
#   START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                            ↓ (RETRY, max 3)
#                                         pre_process

from __future__ import annotations

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.sales_proposal_graph_node import SalesProposalDraftGraphNode
from src.schemas.state import State


class SalesProposalDraftAgent(AgentBaseGraph):
    """Enterprise Sales Proposal Draft Generation Agent (CMN-C2-257).

    Hearing notes → issue extraction → shared-RAG product matching →
    課題→解決策→価値→ROI outline → Markdown draft (S-3 景表法 gate).
    """

    @property
    def name(self) -> str:
        return "cmn_c2_257"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        llm = self.config.get("llm")
        self._nodes["pre_process"] = PreProcessNode(llm=llm, config=self.config)
        self._nodes["main"] = SalesProposalDraftGraphNode(config=self.config)
        self._nodes["post_process"] = PostProcessNode(config=self.config)

    def get_output(self, state: State) -> dict:
        # Framework default omits error_log — surface it so callers can see
        # WHY status=="error" (inner SubgraphError reasons reach here via
        # BaseNode.__call__'s error handling). route()/add_edges() stay
        # framework-owned; get_output() is an override-allowed projection.
        output = super().get_output(state)
        output["error_log"] = state.get("error_log", [])
        return output

    # add_edges() / route() intentionally not overridden (framework backbone).


# Scaffold-compatible alias: src/api/server.py and AgentRegistry both resolve
# the graph via `Graph`.
Graph = SalesProposalDraftAgent
