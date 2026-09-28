"""Unit tests for services: compliance scan, KB contract validation, LLM parsing."""

from __future__ import annotations

import pytest

from src.services.compliance_service import critical_flags, merge_flags, scan_text
from src.services.kb_search_service import (
    CONTRACT_VERSION,
    KBSearchService,
    REQUIRED_METADATA_KEYS,
)
from src.services.llm_service import LLMServiceError, parse_json_payload

from tests.conftest import FakeVectorStore, product_hit, proposal_hit


class TestComplianceService:
    def test_critical_superlative_detected(self):
        flags = scan_text("## 強み\n当社製品は業界No.1の性能です。")
        assert any(f["severity"] == "critical" for f in flags)
        assert flags[0]["section"] == "強み"

    def test_warn_absolute_expression_detected(self):
        flags = scan_text("# 効果\n必ずコストを削減できます。100%保証します。")
        severities = {f["severity"] for f in flags}
        assert "warn" in severities

    def test_clean_text_yields_no_flags(self):
        assert scan_text("## ご提案\n在庫管理を自動化し、棚卸工数の削減が見込めます。") == []

    def test_merge_flags_dedupes(self):
        a = scan_text("業界No.1")
        merged = merge_flags(a, a)
        assert len(merged) == len(a)

    def test_critical_flags_filter(self):
        flags = scan_text("業界No.1。必ず削減。")
        crit = critical_flags(flags)
        assert crit and all(f["severity"] == "critical" for f in crit)


class TestKBSearchService:
    def test_contract_version_pinned(self):
        assert CONTRACT_VERSION == "1.0.0"

    def test_compliant_hits_adopted_with_metadata(self):
        svc = KBSearchService(
            FakeVectorStore([product_hit()]), FakeVectorStore([proposal_hit()])
        )
        found = svc.search_issue("在庫管理")
        assert len(found["solutions"]) == 1
        item = found["solutions"][0]
        for key in REQUIRED_METADATA_KEYS:
            assert item[key]
        assert item["product_name"] == "製品X"
        assert found["rejected"] == []

    def test_missing_required_metadata_rejected(self):
        broken = product_hit(record_id="")  # record_id empty → contract violation
        svc = KBSearchService(FakeVectorStore([broken]), FakeVectorStore([]))
        found = svc.search_issue("在庫管理")
        assert found["solutions"] == []
        assert len(found["rejected"]) == 1
        assert "record_id" in found["rejected"][0]["missing_metadata"]

    def test_missing_title_key_rejected(self):
        no_title = product_hit(product_name="")
        svc = KBSearchService(FakeVectorStore([no_title]), FakeVectorStore([]))
        found = svc.search_issue("x")
        assert found["solutions"] == []
        assert found["rejected"][0]["missing_metadata"] == ["product_name|proposal_title"]

    def test_min_score_filters_low_hits(self):
        svc = KBSearchService(
            FakeVectorStore([product_hit(score=0.1)]), FakeVectorStore([]), min_score=0.5
        )
        found = svc.search_issue("x")
        assert found["solutions"] == [] and found["rejected"] == []

    def test_store_error_propagates(self):
        svc = KBSearchService(
            FakeVectorStore(error=ConnectionError("kb down")), FakeVectorStore([])
        )
        with pytest.raises(ConnectionError):
            svc.search_issue("x")


class TestLLMService:
    def test_parse_plain_json(self):
        assert parse_json_payload('{"a": 1}') == {"a": 1}

    def test_parse_fenced_json(self):
        assert parse_json_payload('```json\n{"a": 1}\n```') == {"a": 1}

    def test_parse_json_with_surrounding_prose(self):
        assert parse_json_payload('結果:\n{"a": 1}\n以上') == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(LLMServiceError):
            parse_json_payload("not json at all")
