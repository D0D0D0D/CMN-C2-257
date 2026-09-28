"""End-to-end invoke through the outer 5-node backbone (fakes injected)."""

from __future__ import annotations

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.services.kb_search_service import KBSearchService

from tests.conftest import FakeLLM, FakeVectorStore, product_hit, proposal_hit

_NOTES = "商談メモ: 棚卸に月3人日かかっており、担当者しか在庫状況が分からない。"


def _agent(llm=None, product_hits=None, proposal_hits=None):
    kb = KBSearchService(
        FakeVectorStore(product_hits if product_hits is not None else [product_hit()]),
        FakeVectorStore(proposal_hits if proposal_hits is not None else [proposal_hit()]),
    )
    agent = Graph(config={"llm": llm or FakeLLM(), "kb_service": kb})
    agent.compile()
    return agent


def _ctx():
    return InvocationContext(
        session_id="it-session",
        caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
    )


class TestHappyPath:
    def test_full_invoke_produces_draft(self):
        result = _agent().invoke(_NOTES, ctx=_ctx())
        assert result["status"] == AgentStatus.SUCCESS.value
        output = result.get("output") or result.get("result") or ""
        assert "ご提案" in output
        assert "> 出典: catalog_x.pdf" in output
        assert "ドラフト" in output  # human-review disclaimer

    def test_all_five_backbone_nodes_ran(self):
        result = _agent().invoke(_NOTES, ctx=_ctx())
        history = " ".join(str(h) for h in result.get("node_history", []))
        for node in ("Initialize", "PreProcess", "SalesProposalDraftGraphNode", "PostProcess", "Finalize"):
            assert node.lower().replace("node", "") in history.lower().replace("node", ""), (
                f"{node} missing from node_history: {history}"
            )


class TestUnmatchedFlow:
    def test_zero_hits_yields_confirmation_summary_not_error(self):
        result = _agent(product_hits=[], proposal_hits=[]).invoke(_NOTES, ctx=_ctx())
        assert result["status"] == AgentStatus.SUCCESS.value
        output = result.get("output") or ""
        assert "該当情報なし・要確認" in output
        assert "要確認課題" in output

    def test_contract_violating_chunk_reported(self):
        result = _agent(product_hits=[product_hit(source_doc="")], proposal_hits=[]).invoke(
            _NOTES, ctx=_ctx()
        )
        assert result["status"] == AgentStatus.SUCCESS.value
        assert "不採用" in (result.get("output") or "")


class TestErrorFlows:
    def test_llm_extraction_failure_is_error_status(self):
        result = _agent(llm=FakeLLM(fail_on={"extract"})).invoke(_NOTES, ctx=_ctx())
        assert result["status"] == AgentStatus.ERROR.value

    def test_critical_compliance_violation_blocks_output(self):
        llm = FakeLLM(section_text="当社製品は業界No.1のシェアを誇ります。")
        result = _agent(llm=llm).invoke(_NOTES, ctx=_ctx())
        assert result["status"] == AgentStatus.ERROR.value

    def test_error_reason_surfaces_in_output_error_log(self):
        result = _agent(llm=FakeLLM(fail_on={"extract"})).invoke(_NOTES, ctx=_ctx())
        assert result["status"] == AgentStatus.ERROR.value
        assert any("issue extraction failed" in e for e in result.get("error_log", []))

    def test_kb_connection_failure_propagates_to_error(self):
        kb = KBSearchService(
            FakeVectorStore(error=ConnectionError("kb down")), FakeVectorStore([])
        )
        agent = Graph(config={"llm": FakeLLM(), "kb_service": kb})
        agent.compile()
        result = agent.invoke(_NOTES, ctx=_ctx())
        assert result["status"] == AgentStatus.ERROR.value

    def test_anonymous_caller_refused_by_s1(self):
        agent = _agent()
        result = agent.invoke(
            _NOTES,
            ctx=InvocationContext(session_id="s", caller_trust_level=TrustLevel.ANONYMOUS),
        )
        assert result["status"] == AgentStatus.ERROR.value


class TestStateSafety:
    def test_final_state_contains_no_secret_shaped_values(self):
        result = _agent().invoke(_NOTES, ctx=_ctx())
        rendered = str(result)
        assert "ANTHROPIC_API_KEY" not in rendered
        assert "VECTOR_STORE_DSN" not in rendered
        assert "SALES_KB_EMBEDDING_API_KEY" not in rendered
