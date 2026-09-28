"""Inner workflow nodes: product_match / proposal_structure / document_gen."""

from __future__ import annotations

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.document_gen_node import DocumentGenNode
from src.nodes.product_match_node import ProductMatchNode
from src.nodes.proposal_structure_node import ProposalStructureNode
from src.services.kb_search_service import KBSearchService

from tests.conftest import FakeLLM, FakeVectorStore, product_hit, proposal_hit


def _payload(issues=None, fmt=None):
    return json.dumps(
        {
            "validated_input": "正規化済みメモ",
            "customer_issues": issues
            if issues is not None
            else [{"issue": "在庫管理の工数過多", "context": "", "priority": "high"}],
            "proposal_format": fmt,
        },
        ensure_ascii=False,
    )


def _kb(product_hits=None, proposal_hits=None):
    return KBSearchService(
        FakeVectorStore(product_hits if product_hits is not None else [product_hit()]),
        FakeVectorStore(proposal_hits if proposal_hits is not None else [proposal_hit()]),
    )


class TestProductMatchNode:
    def test_trust_level(self):
        assert ProductMatchNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL

    def test_upstream_error_passthrough(self):
        node = ProductMatchNode(kb_service=_kb())
        assert node.execute({"status": AgentStatus.ERROR.value}) == {}

    def test_invalid_json_payload_errors(self):
        node = ProductMatchNode(kb_service=_kb())
        out = node.execute({"user_input": "課題テキストそのまま"})
        assert out["status"] == AgentStatus.ERROR

    def test_restores_payload_and_searches(self):
        node = ProductMatchNode(kb_service=_kb())
        out = node.execute({"user_input": _payload(fmt={"tone": "formal"})})
        assert out["status"] == AgentStatus.SUCCESS
        assert out["customer_issues"][0]["issue"] == "在庫管理の工数過多"
        assert out["proposal_format"] == {"tone": "formal"}
        assert out["matched_solutions"][0]["record_id"] == "P-001"
        assert out["matched_stories"][0]["record_id"] == "PR-001"
        assert out["unmatched_issues"] == []

    def test_zero_hits_quarantined_not_guessed(self):
        node = ProductMatchNode(kb_service=_kb(product_hits=[], proposal_hits=[]))
        out = node.execute({"user_input": _payload()})
        assert out["status"] == AgentStatus.SUCCESS
        assert out["matched_solutions"] == []
        assert len(out["unmatched_issues"]) == 1
        assert "要確認" in out["unmatched_issues"][0]["reason"]

    def test_contract_violating_chunks_rejected(self):
        node = ProductMatchNode(
            kb_service=_kb(product_hits=[product_hit(chunk_hash="")], proposal_hits=[])
        )
        out = node.execute({"user_input": _payload()})
        assert out["kb_rejected_chunks"]
        assert "chunk_hash" in out["kb_rejected_chunks"][0]["missing_metadata"]
        assert len(out["unmatched_issues"]) == 1  # nothing adopted → 要確認

    def test_empty_issues_errors(self):
        node = ProductMatchNode(kb_service=_kb())
        out = node.execute({"user_input": _payload(issues=[])})
        assert out["status"] == AgentStatus.ERROR


class TestProposalStructureNode:
    def test_upstream_error_passthrough(self):
        assert ProposalStructureNode(llm=FakeLLM()).execute(
            {"status": AgentStatus.ERROR.value}
        ) == {}

    def test_outline_composed(self):
        node = ProposalStructureNode(llm=FakeLLM())
        out = node.execute(
            {
                "customer_issues": [{"issue": "在庫管理の工数過多"}],
                "matched_solutions": [{"record_id": "P-001"}],
                "matched_stories": [],
                "unmatched_issues": [],
            }
        )
        assert out["status"] == AgentStatus.SUCCESS
        assert out["proposal_outline"]["sections"]

    def test_unmatched_issues_get_explicit_section(self):
        node = ProposalStructureNode(llm=FakeLLM())
        out = node.execute(
            {
                "customer_issues": [{"issue": "x"}],
                "matched_solutions": [],
                "matched_stories": [],
                "unmatched_issues": [{"issue": "x", "reason": "0件"}],
            }
        )
        headings = [s["heading"] for s in out["proposal_outline"]["sections"]]
        assert "該当情報なし・要確認" in headings

    def test_llm_failure_errors(self):
        node = ProposalStructureNode(llm=FakeLLM(fail_on={"outline"}))
        out = node.execute({"customer_issues": [{"issue": "x"}]})
        assert out["status"] == AgentStatus.ERROR


class TestDocumentGenNode:
    def _state(self, llm_hits=True, unmatched=None):
        solutions = (
            [{"record_id": "P-001", "kb_type": "product", "product_name": "製品X",
              "proposal_title": "", "content": "製品Xは在庫管理を自動化", "source_doc": "catalog_x.pdf"}]
            if llm_hits
            else []
        )
        sections = [
            {"heading": "ご提案", "issue": "在庫", "solution_record_ids": ["P-001"],
             "purpose": "solution", "notes": ""}
        ]
        if unmatched:
            sections.append(
                {"heading": "該当情報なし・要確認", "issue": "", "solution_record_ids": [],
                 "purpose": "unmatched", "notes": ""}
            )
        return {
            "proposal_outline": {"title": "ご提案書", "sections": sections},
            "matched_solutions": solutions,
            "matched_stories": [],
            "unmatched_issues": unmatched or [],
        }

    def test_upstream_error_passthrough(self):
        assert DocumentGenNode(llm=FakeLLM()).execute({"status": AgentStatus.ERROR.value}) == {}

    def test_draft_has_citations(self):
        node = DocumentGenNode(llm=FakeLLM())
        out = node.execute(self._state())
        assert out["status"] == AgentStatus.SUCCESS
        assert "> 出典: catalog_x.pdf" in out["proposal_draft"]
        assert "## ご提案" in out["proposal_draft"]

    def test_unmatched_section_rendered_without_llm_guess(self):
        node = DocumentGenNode(llm=FakeLLM())
        out = node.execute(self._state(unmatched=[{"issue": "特殊要件", "reason": "0件"}]))
        assert "該当情報なし・要確認" in out["proposal_draft"]
        assert "特殊要件" in out["proposal_draft"]

    def test_compliance_first_pass_flags_in_state(self):
        node = DocumentGenNode(llm=FakeLLM(section_text="当社は業界No.1です。"))
        out = node.execute(self._state())
        assert out["compliance_flags"]
        assert out["compliance_flags"][0]["severity"] == "critical"

    def test_missing_outline_errors(self):
        out = DocumentGenNode(llm=FakeLLM()).execute({})
        assert out["status"] == AgentStatus.ERROR

    def test_llm_failure_during_generation_errors(self):
        node = DocumentGenNode(llm=FakeLLM(fail_on={"section"}))
        out = node.execute(self._state())
        assert out["status"] == AgentStatus.ERROR
        assert any("draft generation failed" in e for e in out["error_log"])
