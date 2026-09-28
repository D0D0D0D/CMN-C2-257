"""Shared test fixtures — fake LLM / fake vector stores (no network, no secrets)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# repo root on sys.path (wheel-era: framework/shared come from site-packages)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from framework.schemas.trust_level import TrustLevel
from shared.services.llm.base_llm import BaseLLM
from shared.services.vector_store.base_vector_store import BaseVectorStore


class FakeLLM(BaseLLM):
    """Prompt-keyed fake. Recognizes the three node prompts by marker text."""

    def __init__(self, issues=None, outline=None, section_text="製品Xの説明です。", fail_on=None):
        self.issues = issues if issues is not None else [
            {"issue": "在庫管理の工数過多", "context": "月次棚卸に3人日", "priority": "high"}
        ]
        self.outline = outline
        self.section_text = section_text
        self.fail_on = fail_on or set()
        self.calls: list[str] = []

    def complete(self, messages: list) -> dict:
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "課題分析" in system:
            self.calls.append("extract")
            if "extract" in self.fail_on:
                return {"content": "not-json"}
            return {"content": json.dumps({"customer_issues": self.issues}, ensure_ascii=False)}
        if "ストーリー設計" in system:
            self.calls.append("outline")
            if "outline" in self.fail_on:
                return {"content": "not-json"}
            outline = self.outline or self._default_outline(user)
            return {"content": json.dumps(outline, ensure_ascii=False)}
        if "ライター" in system:
            self.calls.append("section")
            if "section" in self.fail_on:
                return {"content": ""}
            return {"content": self.section_text}
        raise AssertionError(f"unexpected system prompt: {system[:60]}")

    def _default_outline(self, user: str) -> dict:
        material = json.loads(user)
        record_ids = [h["record_id"] for h in material.get("matched_solutions", [])]
        return {
            "title": "御社向けご提案",
            "sections": [
                {
                    "heading": "課題の整理",
                    "issue": "在庫管理の工数過多",
                    "solution_record_ids": [],
                    "purpose": "issue",
                    "notes": "",
                },
                {
                    "heading": "ご提案ソリューション",
                    "issue": "在庫管理の工数過多",
                    "solution_record_ids": record_ids,
                    "purpose": "solution",
                    "notes": "",
                },
            ],
        }

    def stream(self, messages: list):
        yield self.complete(messages)["content"]

    def bind_tools(self, tools: list) -> "FakeLLM":
        return self


class FakeVectorStore(BaseVectorStore):
    def __init__(self, hits=None, error: Exception | None = None):
        self.hits = hits or []
        self.error = error
        self.queries: list[str] = []

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        if self.error:
            raise self.error
        self.queries.append(query)
        return self.hits[:top_k]

    def upsert(self, documents: list) -> None:  # pragma: no cover
        pass

    def delete(self, ids: list) -> None:  # pragma: no cover
        pass


def product_hit(record_id="P-001", source_doc="catalog_x.pdf", name="製品X",
                content="製品Xは在庫管理を自動化します。", score=0.9, **overrides):
    metadata = {
        "record_id": record_id,
        "chunk_hash": "hash-1",
        "source_doc": source_doc,
        "kb_type": "product",
        "product_name": name,
    }
    metadata.update(overrides)
    return {"content": content, "score": score, "metadata": metadata}


def proposal_hit(record_id="PR-001", source_doc="proposal_2025.pdf", title="製造業向け提案",
                 content="課題→解決策→ROI の構成で受注。", score=0.8, **overrides):
    metadata = {
        "record_id": record_id,
        "chunk_hash": "hash-2",
        "source_doc": source_doc,
        "kb_type": "proposal",
        "proposal_title": title,
    }
    metadata.update(overrides)
    return {"content": content, "score": score, "metadata": metadata}


VERIFIED = TrustLevel.VERIFIED_EXTERNAL.value


@pytest.fixture
def fake_llm():
    return FakeLLM()


@pytest.fixture
def kb_service():
    from src.services.kb_search_service import KBSearchService

    return KBSearchService(
        product_store=FakeVectorStore([product_hit()]),
        proposal_store=FakeVectorStore([proposal_hit()]),
        top_k=5,
    )


@pytest.fixture
def agent_config(kb_service) -> dict:
    """実 LLM 1 回転チェック用の Graph 構築 config。

    `tests/integration/test_graph_invoke.py` の `_agent()` と同じく、KB は
    `product_hit()` / `proposal_hit()` を載せた fake store で seed する。
    ハーネスが `config["llm"]` を後から注入するため、ここでは `kb_service`
    だけを渡す。
    """
    return {"kb_service": kb_service}


@pytest.fixture
def case_payload() -> str:
    """統合テスト `test_graph_invoke.py` の `_NOTES` と同じ商談メモ。"""
    return "商談メモ: 棚卸に月3人日かかっており、担当者しか在庫状況が分からない。"
