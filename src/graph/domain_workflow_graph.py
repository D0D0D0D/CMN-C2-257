"""AgentCore Platform v1.0"""

# Inner domain workflow graph for CMN-C2-257 (Cat 2).
# Instantiated (and cached) by SalesProposalDraftGraphNode.get_subgraph().
# Linear topology: START → product_match → proposal_structure → document_gen → END
# Each node passes upstream status=="error" through, so a failure in any
# step reaches get_output() without running the remaining business logic.

from __future__ import annotations

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus

from src.nodes.document_gen_node import DocumentGenNode
from src.nodes.product_match_node import ProductMatchNode
from src.nodes.proposal_structure_node import ProposalStructureNode
from src.schemas.state import State
from src.services.kb_search_service import KBSearchService


class SalesProposalDraftGraph(BaseGraph):
    """Inner graph: RAG search → outline composition → draft generation.

    Dependency injection via config:
      llm            — BaseLLM instance (tests inject fakes; production
                       falls back to build_default_llm())
      kb_service     — KBSearchService instance, or
      product_store / proposal_store — BaseVectorStore pair to wrap
      top_k / min_score / *_kb_collection — search tuning keys
    """

    @property
    def name(self) -> str:
        return "sales_proposal_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        # No mandatory config: llm/kb_service fall back to production
        # defaults at execute() time (secrets via ctx, never State).
        pass

    def register_nodes(self) -> None:
        # No super() call — BaseGraph.register_nodes() is abstract.
        # initialize/finalize are outer backbone concerns.
        llm = self.config.get("llm")
        kb_service = self.config.get("kb_service")
        if kb_service is None and self.config.get("product_store") is not None:
            kb_service = KBSearchService(
                product_store=self.config["product_store"],
                proposal_store=self.config["proposal_store"],
                top_k=int(self.config.get("top_k", 5)),
                min_score=float(self.config.get("min_score", 0.0)),
            )
        self._nodes["product_match"] = ProductMatchNode(
            kb_service=kb_service, config=self.config
        )
        self._nodes["proposal_structure"] = ProposalStructureNode(
            llm=llm, config=self.config
        )
        self._nodes["document_gen"] = DocumentGenNode(llm=llm, config=self.config)

    def add_edges(self) -> None:
        self._sg.add_edge(START, "product_match")
        self._sg.add_edge("product_match", "proposal_structure")
        self._sg.add_edge("proposal_structure", "document_gen")
        self._sg.add_edge("document_gen", END)

    def route(self, state: State) -> str:
        # Required by BaseGraph ABC; never called for this linear topology.
        return END if state.get("status") == AgentStatus.ERROR.value else "document_gen"

    def get_output(self, state: State) -> dict:
        # Designed together with SalesProposalDraftGraphNode.merge_output().
        return {
            "proposal_draft": state.get("proposal_draft", ""),
            "proposal_outline": state.get("proposal_outline", {}),
            "matched_solutions": state.get("matched_solutions", []),
            "matched_stories": state.get("matched_stories", []),
            "unmatched_issues": state.get("unmatched_issues", []),
            "kb_rejected_chunks": state.get("kb_rejected_chunks", []),
            "compliance_flags": state.get("compliance_flags", []),
            "status": state.get("status"),
            "error_log": state.get("error_log", []),
            "trace_id": state.get("trace_id", ""),
            "correlation_id": state.get("correlation_id", ""),
            "node_history": state.get("node_history", []),
        }
