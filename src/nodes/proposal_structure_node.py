"""AgentCore Platform v1.0"""

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.llm_service import (
    LLMServiceError,
    build_default_llm,
    complete_text,
    parse_json_payload,
)

_SYSTEM_PROMPT = (
    "あなたは B2B 営業提案書のストーリー設計者です。"
    "顧客課題と、共通ナレッジベースから検索済みの製品情報・過去提案ストーリーを入力として、"
    "課題→解決策→価値→投資対効果（ROI）の章立て・ストーリーフローを構成してください。"
    "検索済み情報に無い製品・仕様を章立てに割り付けないこと。"
    '出力は次の JSON のみ: {"title": "提案書タイトル", "sections": ['
    '{"heading": "章タイトル", "issue": "対応する課題（無ければ空）", '
    '"solution_record_ids": ["割り付ける製品/過去提案の record_id"], '
    '"purpose": "issue|solution|value|roi|other", "notes": "構成メモ"}]}'
)


class ProposalStructureNode(FunctionNode):
    """Composes the 課題→解決策→価値→ROI chapter/story-flow outline (LLM)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm=None, config: dict | None = None):
        super().__init__()
        self._llm = llm
        self._config = dict(config or {})

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}  # upstream error passthrough

        issues = state.get("customer_issues", [])
        if not issues:
            emit_trace_event("outline_composition_failed", {"reason": "no_issues"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ProposalStructureNode: customer_issues is empty"],
            }

        material = {
            "customer_issues": issues,
            "matched_solutions": state.get("matched_solutions", []),
            "matched_stories": state.get("matched_stories", []),
            "unmatched_issues": state.get("unmatched_issues", []),
            "proposal_format": state.get("proposal_format"),
        }
        try:
            llm = self._llm or build_default_llm(self._config)
            outline = parse_json_payload(
                complete_text(
                    llm,
                    _SYSTEM_PROMPT,
                    json.dumps(material, ensure_ascii=False, default=str),
                )
            )
        except LLMServiceError as e:
            emit_trace_event("outline_composition_failed", {"reason": "llm_error"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"ProposalStructureNode: outline composition failed — {e}"],
            }

        sections = [s for s in outline.get("sections", []) if isinstance(s, dict)]
        if not sections:
            emit_trace_event("outline_composition_failed", {"reason": "zero_sections"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["ProposalStructureNode: LLM returned no sections"],
            }

        # Hallucination guard: unmatched issues always surface as an explicit
        # 要確認 section — never silently dropped, never guessed.
        if state.get("unmatched_issues"):
            sections.append(
                {
                    "heading": "該当情報なし・要確認",
                    "issue": "",
                    "solution_record_ids": [],
                    "purpose": "unmatched",
                    "notes": "共通 RAG にヒットしなかった課題の一覧（人間による確認が必要）",
                }
            )

        proposal_outline = {
            "title": str(outline.get("title", "提案書ドラフト")),
            "sections": sections,
        }
        emit_trace_event(
            "outline_composed",
            {"section_count": len(sections), "title": proposal_outline["title"]},
            state,
        )
        return {"proposal_outline": proposal_outline, "status": AgentStatus.SUCCESS.value}
