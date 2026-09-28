"""AgentCore Platform v1.0"""

# State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from __future__ import annotations

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """CMN-C2-257 Sales Proposal Draft state.

    Flat TypedDict. All shared fields (user_input, validated_input, status,
    session_id, node_history, error_log, result, formatted_output, etc.)
    are inherited from AgentState. VectorStore/LLM credentials are NEVER
    stored here — they arrive via ctx.secrets at the service layer.
    """

    # Optional caller-supplied format hints (chapter structure / tone).
    proposal_format: dict | None

    # Extracted customer pain points: [{"issue", "context", "priority"}].
    customer_issues: list

    # product_kb hits per issue, incl. Data Contract 1.0.0 required metadata
    # (record_id, chunk_hash, source_doc, kb_type, product_name).
    matched_solutions: list

    # proposal_kb hits per issue (record_id, chunk_hash, source_doc,
    # kb_type, proposal_title).
    matched_stories: list

    # Issues with zero compliant RAG hits — rendered as "要確認" sections,
    # never filled by guesswork (hallucination guard).
    unmatched_issues: list

    # RAG hits dropped by contract-compliance validation (missing required
    # metadata) — reported in the confirmation summary (path-(b) drift guard).
    kb_rejected_chunks: list

    # Chapter/story-flow outline: {"title", "sections": [...]}.
    proposal_outline: dict

    # Generated proposal draft (Markdown, with source citations).
    proposal_draft: str

    # 景表法/優良誤認 warnings: [{"section", "expression", "reason", "severity"}].
    # Written by document_gen / post_process execute() business logic only.
    compliance_flags: list
