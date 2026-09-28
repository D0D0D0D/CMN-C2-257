# Test Specification — CMN-C2-257

## AGENTIC STAR Marketplace 実機疎通（2026-08-31・PASS）

AGENTIC STAR の開発環境の User Page から実際に invoke し、runner Pod の実行結果と応答を確認した。

| 項目 | 実測 |
|---|---|
| 入力 | 「自動車部品メーカー向けに、設備の突発停止を減らす予知保全SaaSの提案書ドラフトを作成してください。想定予算は年間2000万円、導入期間は6ヶ月です。」 |
| runner Pod | 実行 25.0 秒 / exit 0 / agent コンテナのログ 0 行（正常完了時は出力しない） |
| 応答 | 日本語の提案書ドラフト（投資対効果の考え方・運用負荷を含む総合評価・目的別の評価軸）＋「該当情報なし・要確認」セクション 3 件 |

「設備の突発停止を減らしたい」「予算上限は年間2000万円である」「導入期間が6ヶ月である」の 3 項目について、共通 RAG に合致する製品/過去提案が無い旨を明示し、推測での記載を行わないことを確認。

KB は未投入のため、確認できたのは「該当情報が無いときに正しくそう返すこと」までである。
KB 投入後の検索精度はこの記録の対象外。


営業提案書ドラフト生成エージェントのテスト仕様。実行方法:
`python -m pytest tests/ -v` / `python -m pytest tests/proof_of_boundary/ -v`
（LLM / VectorStore は fake 注入 — SDK と同じ Qdrant hit 形状を adapter に通し、ネットワーク・実 secrets は不要）


## 実 LLM 1 回転 + アダプタ実証（2026-08-31・PASS）

| 層 | 実物か | 根拠 |
|---|---|---|
| LLM | ✅ 実物 | 実 LLM を刺した `BaseLLM` 実装で 1 回転。1114 字の実出力 |
| Qdrant store | ❌ SDK 形状の fake | 実 Qdrant を起動できないため。**SDK `QdrantVectorStore` と同じ hit 形状** `{id, payload, score, similarity}` を返す fake を `SalesKBQdrantAdapter` に通した |

アダプタが**読み側の必須キーを全て復元する**ことを名指しで検査した:
`record_id` / `chunk_hash` / `source_doc` / `kb_type` / `product_name` / `proposal_title` の 6 キー ＋ `content` / `score` = **8/8 復元**。

変異検査: `payload → metadata` の写しを無効化すると
`test_qdrant_payload_is_mapped_to_every_reader_required_key` が FAILED（実装者とレビュアーが独立に確認）。

既知の限界: store が fake のため、実 Qdrant 接合（接続・collection 作成・埋め込み生成）は未検証。

## Test Strategy
- Coverage target: 80%
- Test types: Unit（nodes / services）/ Integration（外側 5 ノード backbone の full invoke）/ Proof-of-Boundary
- Retrofit mutation: adapter の `payload → metadata` 写しを空 dict に変異させ、専用 test の失敗を確認後に復元する。

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | ✅ `tests/unit/test_state.py` |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | ✅ `test_post_process_node.py::test_critical_violation_blocked` |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations | ✅ CI + `test_graph_invoke.py::TestStateSafety` |
| TC-04 | InvocationContext via configurable only | Direct access raises error | ✅ State に ctx 非格納（`test_state.py`）+ PB-2 |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | ✅ 0 duplicates（PB-1 走査） |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` at class definition if overridden | ✅ 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` at class definition if overridden | ✅ 0 overrides |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused before `execute()`; `execute()` が呼ばれなかったことを証明する | ✅ `test_pre_process_node.py::test_s1_low_trust_refused` + `test_graph_invoke.py::test_anonymous_caller_refused_by_s1` + PB-6 S-1 denial fixture |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | 入力サイズ上限（既定 PII scan の後段） | ✅ `test_s2_extra_gate_rejects_oversized_input` |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | 重大違反ブロック + 残存 PII マスキング（検証専用・State 非書込） | ✅ `TestPostProcessS3Gate` |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | issues_extracted / kb_search_completed / outline_composed / draft_generated / compliance_checked | ✅ 各ノード実装 + PB-1 |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → AuditLogger | `emit_trace_event()` fires | No silent failures | ✅ `tests/proof_of_boundary/` |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | ✅ `test_state_safety.py` |
| PB-3 | L1 → External service | SDK `QdrantVectorStore` 経由の検索（SDK hit 形状 fake / 顧客環境で実接続） | Data retrieved | ✅ adapter + fake 注入で契約検証（実 Qdrant は顧客環境前提 — docs/07 参照） |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | ✅ `test_import_isolation.py` + CI |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | ✅ `test_state_safety.py`（hitl 無効・checkpointer 未使用） |
| PB-6 | Invoke execution order | S-1 → S-4 node_start → S-2 → execute() → S-3 → S-4 node_complete | Order verified | ✅ `test_pb_invoke_order.py`（src/nodes 全ノード自動発見） |
| PB-7 | HITL interrupt propagation | — | — | N/A（`hitl.enabled: false`） |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | 課題抽出 | ヒアリング記録（非構造） | customer_issues 抽出・0 件は error | ✅ `test_pre_process_node.py` |
| BL-02 | 内側 JSON 復元契約 | extract_input() の JSON payload | customer_issues/proposal_format 展開・不正 JSON は error | ✅ `test_inner_nodes.py::TestProductMatchNode` / `test_graph_node.py` |
| BL-03 | Contract 準拠検証（ドリフト防御） | 必須 metadata 欠落 chunk（Data Contract 1.0.0 §6 の 5 キー） | 不採用 + kb_rejected_chunks 計上 | ✅ `test_services.py::TestKBSearchService` / `test_inner_nodes.py` |
| BL-04 | RAG ヒット 0 件の隔離 | 全 collection 0 件 | unmatched_issues 隔離・推測生成なし・status=success | ✅ `test_inner_nodes.py` / `test_graph_invoke.py::TestUnmatchedFlow` |
| BL-05 | 章立て構成 | 課題 + 検索結果 | 課題→解決策→価値→ROI sections + 要確認節 | ✅ `test_inner_nodes.py::TestProposalStructureNode` |
| BL-06 | 出典併記 | 採用 chunk あり | `> 出典: {source_doc}` を各章に併記 | ✅ `test_inner_nodes.py::test_draft_has_citations` |
| BL-07 | 景表法スクリーニング | 「業界No.1」等 | critical flag（execute 業務ロジックで State 書込） | ✅ `test_services.py::TestComplianceService` / `test_inner_nodes.py` |
| BL-08 | 重大違反ブロック | critical flag 残存 | SecurityViolationError → status=error | ✅ `test_post_process_node.py` / `test_graph_invoke.py` |
| BL-09 | 警告サマリ・免責 | warn flag / 要確認 / 不採用あり | result 冒頭に要修正一覧・件数・ドラフト免責 | ✅ `test_post_process_node.py` |
| BL-10 | KB 接続失敗の propagate | store が例外 | SubgraphError propagate → status=error | ✅ `test_graph_invoke.py::test_kb_connection_failure_propagates_to_error` |
| BL-11 | PII マスキング | email/電話番号入り入力・出力 | S-2 既定ゲートで [MASKED]・S-3 hook で残存マスク | ✅ `test_pre_process_node.py` / `test_post_process_node.py` |
| BL-12 | SDK Qdrant hit adapter | `{id,payload,score,similarity}` | `payload.content` と必須 metadata を既存 hit 形状へ欠落なく写す | ✅ `test_sales_kb_qdrant_adapter.py` |
| BL-13 | 未設定/明示無効の縮退 | vector_store section なし / `enabled: false` | 空検索となり既存 zero-hit/unmatched 契約を維持 | ✅ `test_server_vector_wiring.py` |
| BL-14 | 有効だが不正な設定 | `enabled: true` + 必須 config 欠落 / constructor error | startup fail-fast、空検索へ偽装しない | ✅ `test_server_vector_wiring.py` |
| BL-15 | SDK/runtime lifecycle | 有効 config + fake Qdrant/runtime | embedding secret 注入、2 collection 配線、shutdown close | ✅ `test_server_vector_wiring.py` |
| BL-16 | deployment SDK 版一致 | CI / local-STG の wheel spec | 全経路が extras 付き 1.0.2 に一致 | — 廃止（CI 設定を scaffold の共通定義に切り替えたため廃止。版は共通定義が一元管理する） |

## 実 LLM 1 回転チェック（Live LLM Verification）

`config/agent.yaml` の `generation_mode: "llm"` 宣言に対する実測記録。上表の TC/BL は
すべて fake LLM 注入なので、「プロンプトが出力契約を実際に導けるか」は検証できない
（モックは常に整形済み JSON を返す）。ここでは実 LLM を注入シームへ刺して 1 回だけ
invoke し、使える出力が出ることを確かめた。

| 項目 | 実測値 |
|------|--------|
| 実施日 | 2026-08-20 |
| 使用 LLM | Azure OpenAI 上の GPT モデル — 検証専用の `BaseLLM` 実装 |
| 注入シーム | `config["llm"]`（`SalesProposalDraftAgent.register_nodes()` が `self.config.get("llm")` を 3 ノードと内側グラフへ配る） |
| 入力 | `tests/conftest.py` の `case_payload` / `agent_config`（統合テスト `test_graph_invoke.py` と同一の商談メモ + fake KB seed） |
| 実 LLM 呼び出し回数 | **6 回**（課題抽出 1 + 章立て構成 1 + 本文執筆 4 章） |
| 結果 | ✅ `status: success` |
| 非空だった出力キー | `output`（Markdown ドラフト 895 字・`result` / `formatted_output` と同一本文） |
| 縮退 | なし（`degradation_reason` なし・`compliance_flags` 0 件） |

実行手段: 開発者手元での検証スクリプト（CI には入れない — 外部 LLM の CLI と API キーを要するため）。

**実 LLM でのみ確認できたこと**（fake では出ない性質）:

- 3 種のプロンプトが、いずれも出力契約を LLM に正しく伝えられている。課題抽出・章立ての
  2 プロンプトは JSON のみを返させることに成功し（`parse_json_payload` のフェンス剥がしに
  頼らず素の JSON が返った）、本文執筆プロンプトは指示どおり見出し行を出さず本文のみを返した。
- **接地（grounding）が効いている。** 検索済みソース（`製品Xは在庫管理を自動化します。`）に
  無い仕様・数値を LLM が創作しなかった。ROI 章では削減率・人日単価・費用を仮定せず
  「導入後の実績値と提示された費用情報を用いて算定する」と算定式のみを提示しており、
  「ソースに無い数値は書かない」という禁止がプロンプトで実際に守られている。
- **S-3 景表法ゲートに実 LLM 出力が抵触しなかった。** `document_gen` プロンプトの
  最上級・断定表現の禁止（`業界No.1` / `必ず` / `100%保証` 等）が効き、`compliance_flags` は
  0 件・`PostProcessNode._extra_security_gate_output()` のブロックも発火しなかった
  （営業文書という題材上、実 LLM が最も踏みやすい罠だが踏まなかった）。

`src/` への修正は不要だった。fixture も既存の `agent_config` / `case_payload` を
そのまま使用（`live_input_context` は未定義＝既定の `{"mode": "invoke"}`。本 AG は
`input_context` から `proposal_format` しか読まないため統合テストと等価）。

## Test Execution Summary
- Execution date: 2026-08-31
- Baseline before retrofit: 101 passed / 2 skipped
- Retrofit result: 110 passed / 2 skipped
- Mutation verification: `SalesKBQdrantAdapter` の payload 写しを `metadata = {}` に変異すると
  `test_qdrant_payload_is_mapped_to_every_reader_required_key` が意図どおり失敗（1 failed / 1 passed）。
  元の `dict(payload)` 写しへ復元後は対象 2 passed、最終全テストも 110 passed / 2 skipped。
