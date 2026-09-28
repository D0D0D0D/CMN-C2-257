"""AgentCore Platform v1.0"""

from __future__ import annotations

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.compliance_service import scan_text
from src.services.llm_service import (
    LLMServiceError,
    build_default_llm,
    complete_text,
)

_SYSTEM_PROMPT = (
    "あなたは B2B 営業提案書のライターです。指定された 1 つの章の本文を Markdown で執筆してください。"
    "厳守事項: (1) 製品の仕様・機能・数値は、提供された検索済みソース（sources）からのみ引用する。"
    "ソースに無い仕様・数値・機能は一切書かない（推測禁止）。"
    "(2) 「業界No.1」「日本一」「必ず」「100%保証」等の根拠なき最上級・断定表現を使わない。"
    "(3) 見出し行は出力せず、本文のみを出力する。"
)


class DocumentGenNode(FunctionNode):
    """Generates the proposal draft section-by-section (LLM).

    Product specs are grounded exclusively in adopted RAG sources; each
    section carries 出典 (source_doc) citations. First-pass 景表法 screening
    runs here in execute() and writes compliance_flags via the returned
    partial dict — gate hooks never write State (docs/02_design.md S-3).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, llm=None, config: dict | None = None):
        super().__init__()
        self._llm = llm
        self._config = dict(config or {})

    def execute(self, state: dict) -> dict:
        if state.get("status") == AgentStatus.ERROR.value:
            return {}  # upstream error passthrough

        outline = state.get("proposal_outline") or {}
        sections = outline.get("sections") or []
        if not sections:
            emit_trace_event("draft_generation_failed", {"reason": "no_outline"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["DocumentGenNode: proposal_outline has no sections"],
            }

        by_record: dict = {}
        for hit in list(state.get("matched_solutions", [])) + list(state.get("matched_stories", [])):
            by_record.setdefault(hit.get("record_id"), hit)

        try:
            llm = self._llm or build_default_llm(self._config)
            parts: list[str] = [f"# {outline.get('title', '提案書ドラフト')}"]
            for section in sections:
                heading = str(section.get("heading", "")).strip() or "(無題)"
                if section.get("purpose") == "unmatched":
                    parts.append(f"## {heading}")
                    for issue in state.get("unmatched_issues", []):
                        parts.append(
                            f"- **{issue.get('issue', '')}** — 該当情報なし・要確認"
                            "（共通 RAG に合致する製品/過去提案が見つかりませんでした。"
                            "推測での記載は行っていません）"
                        )
                    continue
                sources = [
                    by_record[rid]
                    for rid in section.get("solution_record_ids", [])
                    if rid in by_record
                ]
                body = complete_text(
                    llm,
                    _SYSTEM_PROMPT,
                    json.dumps(
                        {
                            "heading": heading,
                            "purpose": section.get("purpose", ""),
                            "issue": section.get("issue", ""),
                            "notes": section.get("notes", ""),
                            "sources": [
                                {
                                    "record_id": s.get("record_id"),
                                    "kb_type": s.get("kb_type"),
                                    "name": s.get("product_name") or s.get("proposal_title"),
                                    "content": s.get("content"),
                                }
                                for s in sources
                            ],
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                )
                parts.append(f"## {heading}")
                parts.append(body.strip())
                if sources:
                    cites = sorted({s.get("source_doc", "") for s in sources if s.get("source_doc")})
                    parts.append("> 出典: " + " / ".join(cites))
        except LLMServiceError as e:
            emit_trace_event("draft_generation_failed", {"reason": "llm_error"}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"DocumentGenNode: draft generation failed — {e}"],
            }

        proposal_draft = "\n\n".join(parts)
        compliance_flags = scan_text(proposal_draft)

        emit_trace_event(
            "draft_generated",
            {
                "section_count": len(sections),
                "draft_chars": len(proposal_draft),
                "compliance_flags": len(compliance_flags),
            },
            state,
        )
        return {
            "proposal_draft": proposal_draft,
            "compliance_flags": compliance_flags,
            "status": AgentStatus.SUCCESS.value,
        }
