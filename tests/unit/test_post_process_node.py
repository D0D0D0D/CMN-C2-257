"""PostProcessNode: warning summary, S-3 critical block, PII masking."""

from __future__ import annotations

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.post_process_node import PostProcessNode

from tests.conftest import VERIFIED


class TestPostProcessExecute:
    def test_empty_draft_errors(self):
        out = PostProcessNode().execute({"proposal_draft": ""})
        assert out["status"] == AgentStatus.ERROR

    def test_result_contains_disclaimer_and_draft(self):
        out = PostProcessNode().execute(
            {"proposal_draft": "# 提案\n\n## 課題\n棚卸工数の削減。"}
        )
        assert out["status"] == AgentStatus.SUCCESS
        assert "ドラフト" in out["result"]
        assert "## 課題" in out["result"]
        assert out["formatted_output"] == out["result"]

    def test_warning_summary_lists_flags(self):
        out = PostProcessNode().execute(
            {"proposal_draft": "## 効果\n必ずコストを削減します。", "compliance_flags": []}
        )
        assert "要修正" in out["result"]
        assert any(f["severity"] == "warn" for f in out["compliance_flags"])

    def test_unmatched_and_rejected_counted_in_summary(self):
        out = PostProcessNode().execute(
            {
                "proposal_draft": "# 提案\n本文",
                "unmatched_issues": [{"issue": "x"}],
                "kb_rejected_chunks": [{"kb_type": "product"}],
            }
        )
        assert "要確認課題 1 件" in out["result"]
        assert "不採用" in out["result"]

    def test_upstream_error_passthrough(self):
        assert PostProcessNode().execute({"status": AgentStatus.ERROR.value}) == {}


class TestPostProcessS3Gate:
    def test_trust_level(self):
        assert PostProcessNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL

    def test_critical_violation_blocked(self):
        node = PostProcessNode()
        with pytest.raises(SecurityViolationError):
            node._extra_security_gate_output(
                {
                    "result": "業界No.1です",
                    "compliance_flags": [
                        {"expression": "業界No.1", "severity": "critical", "reason": "r", "section": ""}
                    ],
                }
            )

    def test_warn_flags_pass_gate(self):
        node = PostProcessNode()
        result = {
            "result": "必ず削減します",
            "compliance_flags": [
                {"expression": "必ず", "severity": "warn", "reason": "r", "section": ""}
            ],
        }
        assert node._extra_security_gate_output(dict(result))["result"]

    def test_gate_masks_jp_names_from_kb_content(self):
        node = PostProcessNode()
        out = node._extra_security_gate_output(
            {"result": "過去事例: 田中様の案件では仕様を3ヶ月で導入。", "compliance_flags": []}
        )
        assert "田中" not in out["result"]
        assert "[MASKED]様" in out["result"]
        assert "仕様" in out["result"]  # compound words untouched

    def test_gate_masks_residual_pii(self):
        node = PostProcessNode()
        out = node._extra_security_gate_output(
            {"result": "連絡先: sales@example.com", "compliance_flags": []}
        )
        assert "sales@example.com" not in out["result"]
        assert "[MASKED]" in out["result"]

    def test_critical_via_full_call_becomes_error(self):
        node = PostProcessNode()
        out = node(
            {
                "proposal_draft": "## 強み\n当社は業界No.1です。",
                "caller_trust_level": VERIFIED,
            }
        )
        assert out["status"] == AgentStatus.ERROR.value
