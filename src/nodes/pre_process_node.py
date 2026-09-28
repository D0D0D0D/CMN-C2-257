"""AgentCore Platform v1.0"""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state)
#  - Return the status STRING via AgentStatus.<X>.value — not the bare enum object
#  - Read input_context via state.get("input_context", {}) — read-only

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.pii_masking_service import mask_jp_names
from src.services.llm_service import (
    LLMServiceError,
    build_default_llm,
    complete_text,
    parse_json_payload,
)

_SYSTEM_PROMPT = (
    "あなたは B2B 営業支援の課題分析アシスタントです。"
    "商談後のヒアリング記録・商談メモから、顧客のペインポイント（課題）を抽出してください。"
    "推測で課題を捏造せず、記録に根拠のある課題のみを抽出すること。"
    '出力は次の JSON のみ: {"customer_issues": [{"issue": "課題の要約", '
    '"context": "記録中の根拠", "priority": "high|medium|low"}]}'
)

_DEFAULT_MAX_INPUT_CHARS = 50_000


class PreProcessNode(FunctionNode):
    """Normalizes hearing notes and extracts customer issues (LLM).

    S-2: the framework @final gate masks PII in user_input/validated_input
    automatically; _extra_security_gate_input() adds the input-size limit.
    """

    # Handles raw customer meeting notes — verified callers only.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm=None, config: dict | None = None):
        super().__init__()
        self._llm = llm
        self._config = dict(config or {})

    def execute(self, state: dict) -> dict:
        user_input = state.get("user_input", "")
        if not isinstance(user_input, str) or not user_input.strip():
            emit_trace_event("issue_extraction_failed", {"reason": "empty_input"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input (hearing notes) is empty"],
            }

        normalized = "\n".join(line.rstrip() for line in user_input.strip().splitlines())

        # Optional format hints arrive via read-only input_context.
        input_context = state.get("input_context", {}) or {}
        proposal_format = input_context.get("proposal_format")
        if proposal_format is not None and not isinstance(proposal_format, dict):
            proposal_format = None

        try:
            llm = self._llm or build_default_llm(self._config)
            payload = parse_json_payload(
                complete_text(llm, _SYSTEM_PROMPT, normalized)
            )
        except LLMServiceError as e:
            emit_trace_event("issue_extraction_failed", {"reason": "llm_error"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"PreProcessNode: issue extraction failed — {e}"],
            }

        issues = [
            i for i in payload.get("customer_issues", [])
            if isinstance(i, dict) and str(i.get("issue", "")).strip()
        ]
        if not issues:
            emit_trace_event("issue_extraction_failed", {"reason": "zero_issues"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [
                    "PreProcessNode: no customer issues could be extracted from the hearing notes"
                ],
            }

        emit_trace_event(
            "issues_extracted",
            {"issue_count": len(issues), "input_chars": len(normalized)},
            state,
        )
        return {
            "validated_input": normalized,
            "customer_issues": issues,
            "proposal_format": proposal_format,
            "status": AgentStatus.SUCCESS.value,
        }

    def _extra_security_gate_input(self, state):
        """S-2 domain extension: JP personal-name masking + input size ceiling.

        The framework default gate masks emails/phones/etc. but its name
        pattern is Latin-script only — Japanese contact names in hearing
        notes (「山田様」「佐藤さん」) pass through, so mask them here.
        """
        state = dict(state)
        user_input = state.get("user_input", "")
        if isinstance(user_input, str) and user_input:
            state["user_input"] = mask_jp_names(user_input)

        max_chars = int(self._config.get("max_input_chars", _DEFAULT_MAX_INPUT_CHARS))
        masked_input = state.get("user_input", "")
        if isinstance(masked_input, str) and len(masked_input) > max_chars:
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = list(state.get("error_log", [])) + [
                f"PreProcessNode: input exceeds {max_chars} chars (S-2 size limit)"
            ]
        return state
