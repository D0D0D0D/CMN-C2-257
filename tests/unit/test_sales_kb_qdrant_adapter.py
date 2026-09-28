"""SDK Qdrant hit-shape compatibility for the sales KB consumer."""

from src.services.sales_kb_qdrant_adapter import SalesKBQdrantAdapter


class _SDKStore:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query, top_k=10):
        return self.hits[:top_k]


def test_qdrant_payload_is_mapped_to_every_reader_required_key():
    adapter = SalesKBQdrantAdapter(
        _SDKStore(
            [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "payload": {
                        "content": "製品Xは在庫管理を自動化します。",
                        "record_id": "P-001",
                        "chunk_hash": "hash-1",
                        "source_doc": "catalog_x.pdf",
                        "kb_type": "product",
                        "product_name": "製品X",
                        "proposal_title": "",
                    },
                    "score": 0.91,
                    "similarity": 0.92,
                }
            ]
        )
    )

    hit = adapter.search("在庫管理", top_k=5)[0]

    assert hit["content"] == "製品Xは在庫管理を自動化します。"
    assert hit["score"] == 0.91
    assert hit["id"] == "00000000-0000-0000-0000-000000000001"
    assert hit["similarity"] == 0.92
    assert hit["metadata"] == {
        "content": "製品Xは在庫管理を自動化します。",
        "record_id": "P-001",
        "chunk_hash": "hash-1",
        "source_doc": "catalog_x.pdf",
        "kb_type": "product",
        "product_name": "製品X",
        "proposal_title": "",
    }


def test_non_mapping_or_missing_payload_becomes_empty_content_and_metadata():
    adapter = SalesKBQdrantAdapter(
        _SDKStore([{"id": "bad", "payload": None, "score": 0.5, "similarity": 0.5}])
    )

    assert adapter.search("query") == [
        {"id": "bad", "content": "", "score": 0.5, "similarity": 0.5, "metadata": {}}
    ]
