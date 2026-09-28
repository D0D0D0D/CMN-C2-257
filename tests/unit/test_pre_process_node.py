"""PreProcessNode: issue extraction, S-2 hooks, trust level (TC-02/05/08)."""

from __future__ import annotations

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.pre_process_node import PreProcessNode

from tests.conftest import VERIFIED, FakeLLM


class TestPreProcessExecute:
    def test_empty_input_returns_error(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node.execute({"user_input": "   "})
        assert out["status"] == AgentStatus.ERROR
        assert out["error_log"]

    def test_extracts_issues_and_normalizes(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node.execute({"user_input": "商談メモ: 棚卸に3人日かかっている  \n改善したい"})
        assert out["status"] == AgentStatus.SUCCESS
        assert out["customer_issues"][0]["issue"]
        assert "  \n" not in out["validated_input"]

    def test_proposal_format_taken_from_input_context(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node.execute(
            {"user_input": "メモ", "input_context": {"proposal_format": {"tone": "formal"}}}
        )
        assert out["proposal_format"] == {"tone": "formal"}

    def test_llm_garbage_returns_error(self):
        node = PreProcessNode(llm=FakeLLM(fail_on={"extract"}))
        out = node.execute({"user_input": "メモ"})
        assert out["status"] == AgentStatus.ERROR

    def test_zero_issues_returns_error(self):
        node = PreProcessNode(llm=FakeLLM(issues=[]))
        out = node.execute({"user_input": "メモ"})
        assert out["status"] == AgentStatus.ERROR


class TestPreProcessGates:
    def test_trust_level_declared_verified_external(self):
        assert PreProcessNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL

    def test_s1_low_trust_refused(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node({"user_input": "メモ", "caller_trust_level": TrustLevel.ANONYMOUS.value})
        assert out["status"] == AgentStatus.ERROR.value
        assert any("S-1" in e or "trust" in e.lower() for e in out["error_log"])

    def test_s2_default_gate_masks_pii_email(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node(
            {
                "user_input": "担当: yamada@example.com 課題は棚卸工数",
                "caller_trust_level": VERIFIED,
            }
        )
        assert "yamada@example.com" not in out.get("validated_input", "")
        assert "[MASKED]" in out.get("validated_input", "")

    def test_s2_extra_gate_masks_japanese_names(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node(
            {
                "user_input": "先方担当の山田様と佐藤さんが同席。課題は棚卸工数。",
                "caller_trust_level": VERIFIED,
            }
        )
        merged = out.get("validated_input", "")
        assert "山田" not in merged and "佐藤" not in merged
        assert "[MASKED]様" in merged and "[MASKED]さん" in merged

    def test_s2_jp_mask_keeps_compound_words(self):
        node = PreProcessNode(llm=FakeLLM())
        out = node(
            {
                "user_input": "お客様の仕様を確認。同様の課題が皆さんにある。",
                "caller_trust_level": VERIFIED,
            }
        )
        merged = out.get("validated_input", "")
        for word in ("お客様", "仕様", "同様", "皆さん"):
            assert word in merged

    def test_s2_extra_gate_rejects_oversized_input(self):
        node = PreProcessNode(llm=FakeLLM(), config={"max_input_chars": 10})
        out = node({"user_input": "x" * 11, "caller_trust_level": VERIFIED})
        assert out["status"] == AgentStatus.ERROR.value
        assert any("size limit" in e for e in out["error_log"])
