"""AgentCore Platform v1.0"""

from __future__ import annotations

from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.security.pii_detector import detect_pii
from shared.utils.audit_logger import emit_trace_event

from src.services.compliance_service import critical_flags, merge_flags, scan_text
from src.services.pii_masking_service import mask_jp_names

_DISCLAIMER = (
    "> ⚠️ 本文書は AI が生成した**ドラフト**です。顧客提出前に、営業担当・営業マネージャーによる"
    "内容確認（特に製品仕様・数値・表現）が必要です。"
)


class PostProcessNode(FunctionNode):
    """S-3 final stage: compliance re-check, warning summary, Markdown assembly.

    Detection (writes compliance_flags/result) lives in execute();
    _extra_security_gate_output() is validation-only — it blocks critical
    景表法 violations (SecurityViolationError) and masks residual PII.
    The framework @final gate has already scanned for credentials.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None):
        super().__init__()
        self._config = dict(config or {})

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}  # defensive passthrough (route already skips on error)

        draft = state.get("proposal_draft", "")
        if not isinstance(draft, str) or not draft.strip():
            emit_trace_event("compliance_check_failed", {"reason": "empty_draft"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PostProcessNode: proposal_draft is empty"],
            }

        flags = merge_flags(state.get("compliance_flags", []), scan_text(draft))

        summary_lines = [_DISCLAIMER, ""]
        if flags:
            summary_lines.append("> **要修正（景表法・優良誤認チェック）:**")
            for f in flags:
                where = f" [{f['section']}]" if f.get("section") else ""
                summary_lines.append(
                    f"> - `{f['expression']}`{where} — {f['reason']}（{f['severity']}）"
                )
            summary_lines.append("")
        rejected = state.get("kb_rejected_chunks", [])
        if rejected:
            summary_lines.append(
                f"> **Contract 非準拠の検索結果 {len(rejected)} 件を不採用にしました**"
                "（必須 metadata 欠落・出典不明のため）。データ整備側での確認を推奨します。"
            )
            summary_lines.append("")
        if state.get("unmatched_issues"):
            summary_lines.append(
                f"> **要確認課題 {len(state['unmatched_issues'])} 件**: 共通 RAG に該当情報が"
                "ありませんでした。「該当情報なし・要確認」節を参照してください。"
            )
            summary_lines.append("")

        result = "\n".join(summary_lines) + "\n" + draft

        emit_trace_event(
            "compliance_checked",
            {
                "flag_count": len(flags),
                "critical_count": len(critical_flags(flags)),
                "rejected_chunks": len(rejected),
            },
            state,
        )
        return {
            "result": result,
            "formatted_output": result,
            "compliance_flags": flags,
            "status": AgentStatus.SUCCESS.value,
        }

    def _extra_security_gate_output(self, result: dict) -> dict:
        """S-3 validation-only hook: critical-violation block + PII masking.

        Never writes new State keys — detection already happened in execute().
        """
        criticals = critical_flags(result.get("compliance_flags", []))
        if criticals:
            expressions = ", ".join(f"`{c['expression']}`" for c in criticals[:5])
            raise SecurityViolationError(
                "S-3: 根拠なき比較優良表示（景表法・重大違反）を検出したため出力をブロックしました: "
                f"{expressions}"
            )
        for key in ("result", "formatted_output"):
            value = result.get(key)
            if not isinstance(value, str):
                continue
            findings = [f for f in detect_pii(value) if f["type"] != "name"]
            if findings:
                masked = value
                for f in reversed(findings):
                    masked = masked[: f["start"]] + "[MASKED]" + masked[f["end"]:]
                value = masked
            # Defence-in-depth: JP names arriving via KB content (input-side
            # masking cannot catch them; primary control is Data Contract §7).
            value = mask_jp_names(value)
            if value != result.get(key):
                result = {**result, key: value}
        return result
