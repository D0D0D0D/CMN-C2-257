# Template Design Specification — CMN-C2-257

営業提案書ドラフト生成エージェント（Enterprise Sales Proposal Draft Generation Agent）の設計仕様。

## Position in AgentCore Architecture

- **Agent Class**: `SalesProposalDraftAgent`
- **L1 Base**: `AgentBaseGraph`（Cat 2。`AutonomousBaseGraph` ではない）
- **Three-Layer Separation**:
  - State: flat TypedDict composition（Pydantic 不使用 — msgpack 非互換のため）
  - Node: L1 継承（Template Method: `execute(self, state: dict) -> dict` のみ override）
  - Graph: composition（外側 `AgentBaseGraph` の `register_nodes()` で main スロットに GraphNode を差し込み）

> 本テンプレートは旧 Level 2 base 継承モデルを取らず、**L1 framework を直接継承**する。

## Architecture Overview

Cat 2 の 2 層構成を取る。外側は固定 5 ノード backbone（Cat 1 と同一）で、提案書生成ドメインの複雑さは
`main` スロットの `GraphNode`（内側 domain workflow graph）に閉じ込める。

### Node Configuration（外側 backbone）

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | 実行コンテキスト初期化・correlation_id/session_id 設定 | user_input | node_history 追記 | InitializeNode (default) |
| pre_process | ヒアリング記録・商談メモの正規化・顧客課題抽出（LLM） | user_input, proposal_format | customer_issues, validated_input | PreProcessNode（override execute）|
| main | 内側 domain workflow graph を実行（RAG 検索 → 章立て構成 → ドラフト生成） | validated_input, customer_issues | matched_solutions, matched_stories, unmatched_issues, proposal_outline, proposal_draft, compliance_flags | `SalesProposalDraftGraphNode`（GraphNode）|
| post_process | S-3 ゲート（景表法・優良誤認 + credential/PII scan）・Markdown 最終整形 | proposal_draft, compliance_flags, unmatched_issues | result, formatted_output | PostProcessNode（override execute）|
| finalize | 出力整形・node_history 確定 | result | output | FinalizeNode (default) |
| Marketplace entry adapter | runner の `input_context` を破棄して設計上の既定 `proposal_format=None` だけを再構築し、allowlist 付き環境 provider と呼出時解決の Azure LLM を配線 | 最新 user message | 提案書ドラフト | `cli.MarketplaceGraph`（`invoke` / `provision_secrets` override） |

### Inner Domain Workflow（`src/graph/domain_workflow_graph.py` — `SalesProposalDraftGraph`）

`GraphNode.get_subgraph()` が返す内側グラフ。`BaseGraph` を継承し独自トポロジ（線形）を実装する。

| Inner Node | 主要ロジック | 出力 |
|---|---|---|
| product_match | **内側グラフのエントリノードとして、外側 `extract_input()` が JSON 直列化した payload（`user_input` として受領）を deserialize し、`customer_issues`/`proposal_format` を内側 State に展開する**（内側グラフは `user_input(str)` + ctx のみ受領・外側 State 非伝播のため）。その後、顧客課題ごとに共通 RAG `product_kb`（製品カタログ）/`proposal_kb`（過去提案ストーリー）を `shared.services.vector_store` で検索。ヒットは Data Contract の必須 metadata（`record_id`/`chunk_hash`/`source_doc`/`kb_type`/`product_name` or `proposal_title`）とともに保持。**ヒット 0 件の課題は `unmatched_issues` に隔離**（推測生成しない） | customer_issues, proposal_format, matched_solutions, matched_stories, unmatched_issues |
| proposal_structure | 課題→解決策→価値→ROI の営業提案固有の章立て・ストーリーフローを構成（LLM）。`proposal_format`（章構成ヒント・トーン）があれば反映。マッチした製品/過去提案を各章に割り付け。`unmatched_issues` は「該当情報なし・要確認」節として構成に含める | proposal_outline |
| document_gen | 章立てに沿って提案書ドラフト（Markdown）をセクション分割で生成（LLM）。**製品仕様の記述は RAG 検索結果の出典に根拠限定**し、各製品仕様記述に出典（`product_kb` の `source_doc`）を併記。`execute()` の業務ロジックとして誇大・断定表現の一次スクリーニングを実施し `compliance_flags` を State に書き込む（ゲートフックでの State 書き込みはしない — 後述 S-3 参照） | proposal_draft, compliance_flags |

内側パイプライン: `START → product_match → proposal_structure → document_gen → END`

> **JSON 復元の契約**: 外側 `extract_input()` は `{"validated_input": ..., "customer_issues": [...],
> "proposal_format": {...}}` を JSON 直列化（`default=str`）して内側 `invoke()` の `user_input` に渡す。内側の
> エントリノード `product_match` の `execute()` 冒頭でこの JSON を parse して内側 State のキーに展開する。
> parse 失敗（不正 JSON・必須キー欠落）は `status="error"` で早期返却する。

> **ハルシネーション防止の多層設計**:
> ① `document_gen` のプロンプトに「製品仕様は RAG 検索結果の出典からのみ引用し、カタログに無い情報は
> 生成しない」を明示制約。② RAG ヒット 0 件の課題は `product_match` 段階で `unmatched_issues` に隔離し、
> ドラフトには「該当情報なし・要確認」として明示（推測で埋めない）。③ 生成した製品仕様部分に出典
> （`source_doc`）を併記し、営業担当が原典を検証できるようにする。④ 出力は**ドラフト**であることを
> 明示し、人間（営業担当・営業マネージャー）レビューを前提とする。

### Data Flow（外側 backbone）

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry, max 3)
                                          pre_process
```

- `pre_process → main` は無条件辺。`main`（GraphNode）は上流 `status == "error"` を必ずパススルーする
  （内側グラフを起動せず早期返却）。
- `route()` は `main` の後に `state["status"]` を読む（`success` → post_process / `retry` → pre_process /
  それ以外 → finalize）。外側 `add_edges()`/`route()` は **override しない**（framework 既定）。

### State Definition（flat TypedDict, `State(AgentState)`）

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| user_input | str | ヒアリング記録・商談メモ（非構造テキスト） | yes |
| proposal_format | dict \| None | （任意）提案書フォーマット指定（章構成ヒント・トーン） | no |
| validated_input | str | pre_process が正規化した入力（内側 graph へ渡す） | no |
| customer_issues | list[dict] | 抽出した顧客課題（`{issue, context, priority}`） | no |
| matched_solutions | list[dict] | `product_kb` 検索結果（`{issue, content, score}` + Data Contract 必須 metadata: `record_id, chunk_hash, source_doc, kb_type, product_name`） | no |
| matched_stories | list[dict] | `proposal_kb` 検索結果（`{issue, content, score}` + `record_id, chunk_hash, source_doc, kb_type, proposal_title`） | no |
| unmatched_issues | list[dict] | RAG ヒット 0 件で「該当情報なし・要確認」扱いにした課題 | no |
| kb_rejected_chunks | list[dict] | Contract 準拠検証で不採用にした検索結果（必須 metadata 欠落・経路(b) ドリフト防御）。確認サマリに計上 | no |
| proposal_outline | dict | 課題→解決策→価値→ROI の章立て構成案 | no |
| proposal_draft | str | 提案書ドラフト（Markdown・出典併記） | no |
| compliance_flags | list[dict] | 景表法・優良誤認の警告（`{section, expression, reason}`）。`document_gen` / `post_process` の `execute()` 業務ロジックが書き込む | no |
| result | str | 最終提案書ドラフト（S-3 通過後・警告サマリ付き Markdown）。post_process が生成 | no |
| formatted_output | str | 表示用 Markdown 射影。post_process が `result` から生成 | no |
| output | str | 最終出力。finalize（framework の `get_output()` 射影）が `result` から設定 — テンプレは直接書かない。外側 graph は `get_output()` を override して **`error_log` を出力に含める**（エラー理由の可観測性。route/add_edges は override しない） | framework |
| status | str | AgentStatus 値（success/error/retry 等） | framework |

**State Constraints (mandatory):**
- Flat TypedDict のみ（primitives + JSON-serializable 型）。
- JWT / API keys / VectorStore 接続情報を State に置かない（checkpoint DB 漏洩防止）。埋め込み API key は
  Secret Provider `require()` 経由、endpoint/model 等の非 secret は `config/config.yaml` から取得し、
  いずれも State 非格納。
- InvocationContext は `config["configurable"]` 経由のみ（State に置かない）。
- Pydantic / dataclass / 任意 Python オブジェクト不可（msgpack 非互換）。

## Framework Utilization

### Shared Components Used
- [x] InvocationContext（correlation_id, session_id, permissions, credential handle）
- [x] SDK 1.0.2 `QdrantVectorStore` — 共通 RAG `product_kb`/`proposal_kb` の検索（読み取りのみ。投入は整備側 = 経路(a) Enterprise Sales Support Data Foundation Builder Agent / 経路(b) 顧客直接）。SDK hit の `payload` は `SalesKBQdrantAdapter` が既存契約の `metadata`/`content` へ写す。
  - **分離モデル（tenant/scope キーなし・設計）**: `product_kb`/`proposal_kb` は **per-deployment の共有カタログ**。分離境界はデプロイ固有の Qdrant endpoint と SDK の namespace/agent tag であり、per-request の scope キーではない。1顧客内に「混ぜてはいけない下位区画」は存在せず、両コレクションの org-wide 検索が仕様。よって `search()` は tenant/org scope 引数を取らない。`proposal_kb` には org-wide 再利用に適した内容だけを投入し、エンドカスタマーごとの可視性制限が必要な場合は投入側で実施する（本 AG では強制不能）。
- [x] `shared.services.llm` — 課題抽出・章立て構成・ドラフト生成（`BaseLLM` をコンストラクタ注入・テストは fake 注入）
- [x] SecurityViolationError
- [x] **S-2**: 連絡先（email/電話等）は framework 既定ゲート（`@final`）が自動マスキング。`_extra_security_gate_input()` はドメイン拡張として**日本語氏名（敬称付き — 既定ゲートの name パターンはLatin 文字専用で検出不能）のマスキング**と入力サイズ上限の検証を実装。pre_process ノードに実装。`_security_gate_input()` は override しない（`@final`）。
- [x] **S-3（2 段構え・検出とゲートの責務分離）**:
      **検出（State 書き込み）は `execute()` の業務ロジック**、**ゲートフックは検証・ブロック専用**とする
      （`_extra_security_gate_*` は検証フックであり State へ新規キーを書き込む API ではないため）。
      (a) **一次スクリーニング**: 内側 `document_gen` ノードの `execute()` 内で生成ドラフトの誇大・断定表現
      （「業界 No.1」「必ず」「完全に」等の景表法・優良誤認リスク表現）を検出し `compliance_flags` を
      **戻り値の部分 dict として** State に書き込む。
      (b) **最終ゲート**: post_process ノードの `execute()` で最終 Markdown に対し優良誤認表現の再チェックを行い
      警告サマリを `result` に併記（除去でなく**フラグ併記** — ドラフトである旨と要修正箇所を営業担当に明示）。
      同ノードの `_extra_security_gate_output()` は**検証専用**として credential/PII scan（連絡先 + 日本語氏名 —
      KB コンテンツ経由で混入し得るため defence-in-depth として出力側でも再マスク。一次防御は Data Contract §7 の
      投入側 PII 除去）と重大違反（根拠なき比較優良表示等）の最終チェックを行い、違反時は `SecurityViolationError` を
      送出してブロック（上位で `status="error"` に落ちる）。
      いずれも `_security_gate_output()` は override しない（`@final`）。
- [x] **S-4**: `emit_trace_event()` — 各ノード `execute()` 内でドメインイベントを発行（課題抽出・RAG 検索
      （ヒット/0 件別）・章立て構成・ドラフト生成・コンプライアンスチェック）。`node_start`/`node_complete`/
      `node_error` は発行しない（`__call__` が自動発行）。

> **S-2/S-3 gate behaviour**: `PreProcessNode`/`PostProcessNode` は `FunctionNode` サブクラス
> のため framework の `@final` ゲートが自動実行され、ドメイン検査は `_extra_*` フックで拡張する。
> `main` の `SalesProposalDraftGraphNode` は `GraphNode` のため S-2/S-3 は deliberate no-op（内側ノードの
> ゲートが適用済み）。内側の各ノードは `FunctionNode` を継承し `_extra_*` で拡張する。

### Composition Pattern

- **Pattern**: GraphNode（subgraph）。外側 `main` スロットに `SalesProposalDraftGraphNode`（`GraphNode`
  サブクラス）を配置し、内側 `SalesProposalDraftGraph`（`BaseGraph`）を `get_subgraph()` で返す。
  `get_subgraph()` は内側グラフを**キャッシュ**して返す（毎回新規生成しない）。
- **Composition target**: `src/graph/domain_workflow_graph.py`
- **GraphNode 契約**:
  - `extract_input()` — `validated_input` + `customer_issues` + `proposal_format` を JSON 直列化
    （`default=str`）して内側 `invoke()` へ渡す（内側は `user_input(str)` + ctx のみ受領・外側 State は
    非伝播のため）。deserialize は内側エントリノード `product_match` が行う（前述「JSON 復元の契約」）。
  - `merge_output()` — 内側 `get_output()` が返す `sub_result` から `proposal_draft`/`proposal_outline`/
    `matched_solutions`/`matched_stories`/`unmatched_issues`/`compliance_flags`/`status` を外側 State へ
    マップ。**変更したキーのみ返却**（full state 返却禁止）。
- **Error propagation strategy**: **propagate**（default）。内側グラフの例外は `SubgraphError` として
  再送出し fail-fast する。本 AG は読み取り専用（RAG 検索のみ・DB/RAG への書き込みなし）のため部分失敗の
  ロールバック考慮は不要。LLM 呼び出し失敗・RAG 接続失敗は propagate して上位で `status="error"` に落とす。

## 共通 Data Contract との整合（参照側）

本エージェントは **Data Contract（contract_version 1.0.0）** が定める RAG スキーマを
**参照側として遵守**する。契約の対象は Data Contract（スキーマ）であって、Enterprise Sales Support Data Foundation Builder Agent という
エージェント実装ではない。データ投入経路は 2 つ:

- **経路(a)**: 基盤 AG Enterprise Sales Support Data Foundation Builder Agent が非定型営業データを分類・正規化して整備（任意のセットアップヘルパー）
- **経路(b)**: 顧客が Data Contract 準拠の RAG を**直接用意**（既存ナレッジ基盤からのエクスポート等。
  Enterprise Sales Support Data Foundation Builder Agent は不要）

いずれの経路でも本 AG の動作は同一（contract 準拠のみを前提とし、投入者を問わない）。

| 項目 | 契約 | 本 AG での利用 |
|---|---|---|
| RAG collection | `product_kb`（製品カタログ・スペック）/ `proposal_kb`（過去提案ストーリー） | `product_match` ノードが検索。collection 名は `config/config.yaml` で上書き可 |
| 必須 metadata | `record_id`・`chunk_hash`・`source_doc`・`kb_type`（`product`/`proposal`）・`product_name` or `proposal_title` | `source_doc`/`record_id` を出典併記に使用。`kb_type` で製品/提案を判別 |
| 更新の冪等性 | 整備側が `record_id + chunk_hash` で delete+insert 置換（経路(a) は Enterprise Sales Support Data Foundation Builder Agent の quality_check 込み・経路(b) は顧客責務） | 本 AG は読み取りのみのため影響なし（検索時点の最新 chunk を参照） |

> KB が未設定・明示無効・未整備（0 件）の環境では、`product_match` が全課題を `unmatched_issues` として返し、
> 「共通 RAG 未整備・Data Contract 準拠の整備が必要（経路(a) Enterprise Sales Support Data Foundation Builder Agent の利用、または経路(b) 顧客直接
> 用意）」を明示した確認サマリを返す（エラーにしない）。**本 AG は Enterprise Sales Support Data Foundation Builder Agent 無しでも単体導入できる**。
> ただし `vector_store.enabled: true` にした後の設定欠落・型不正・embedding secret 欠落・Qdrant 初期化失敗は
> deployment defect として起動を失敗させる。検索中の接続失敗も既存の propagate 契約に従い invoke を error にする。
>
> **リリース前の確認**: 実 invoke 動作確認では、**Data Contract 準拠の
> `product_kb`/`proposal_kb` が実データで整備済みであること**のみ（Enterprise Sales Support Data Foundation Builder Agent の導入有無は問わない）。
>
> **経路(b) ドリフト防御（Contract 準拠検証）**: `product_match` は検索ヒット chunk の必須 metadata
> （`record_id`/`chunk_hash`/`source_doc`/`kb_type`/`product_name` or `proposal_title` — Data Contract 1.0.0 §6 の必須 5 キー）
> 欠落を検出した場合、その chunk を出典不明として
> **採用せず**（出典併記が成立しないため）、確認サマリに「metadata 不備・Contract 非準拠の可能性」を
> 計上する。本 AG が準拠する contract_version は 1.0.0 であり、Data Contract の改版時に追従する。
>
> **経路(b) 顧客への spec 提供**: 本 AG が消費する RAG 定義（`product_kb`/`proposal_kb` の
> 必須 metadata + 準拠 contract_version）の抜粋を運用ガイドに同梱し、
> 顧客に Enterprise Sales Support Data Foundation Builder Agent リポジトリの取得を要求しない。

## HITL

`hitl.enabled: false`。本 AG の出力は**ドラフト**であり、修正・確定は呼び出し元（営業担当・営業マネージャー）の
責務とする（1 invoke = 1 ドラフト生成で完結・ブロックしない）。`interrupt()` は使用しない。

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only（agents/base/ 不要）

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | 固定パイプラインで特定 UC を完遂する Cat 2。自律ループ不要 |
| Composition pattern | Standalone（main 単一ノード） | GraphNode（内側 3 ノード workflow） | **GraphNode** | RAG 検索→章立て→生成の 3 ステップを内側グラフに分離。単一責任・テスト容易性 |
| RAG ヒット 0 件の扱い | LLM の一般知識で補完生成 | 「該当情報なし・要確認」に隔離（推測生成しない） | **隔離** | 製品仕様のハルシネーションは顧客への誤情報提示に直結する高リスク。カタログ根拠のない仕様は書かない |
| S-3 警告の扱い | 違反表現を自動除去 | フラグ併記 + 重大違反のみブロック | **フラグ併記 + 重大違反ブロック** | ドラフト前提のため文脈を壊す自動除去より、要修正箇所の明示が営業担当の実務に合う。根拠なき比較優良表示等の重大違反のみブロック |
| ドラフト生成方式 | 全文一括生成 | 章立てに沿ったセクション分割生成 | **セクション分割** | 長文 LLM のコストと品質を管理し、章単位で出典制約を適用しやすくする |
| HITL | interrupt() で人間確認 | ドラフト返却で完結（確認は呼び出し元責務） | **呼び出し元責務** | 提案書は本質的にドラフト→人間修正のワークフロー。AG 内でブロックする価値がない |
| データ投入の前提 | Enterprise Sales Support Data Foundation Builder Agent を必須の前段とする | Data Contract 準拠のみを前提（経路(a)/(b) を許容） | **Contract 準拠のみ** | 基盤 AG への隠れ依存を避け、スキーマ契約によって単独導入可能性を保つ |
| VectorStore | SDK 1.0.2 の STUB `PgVectorStore` | SDK 1.0.2 の実装済み `QdrantVectorStore` | **`QdrantVectorStore`** | PgVectorStore は構築できても search が `NotImplementedError`。Qdrant hit は repo 内 adapter で既存 Data Contract 形状へ変換する |
| Qdrant 分離部品 | `qdrant-scoped-backend` 部品 | SDK を直接使用 | **SDK を直接使用** | 本 AG は `SCOPE_KEYS` を持たず、顧客内 KB に混在禁止の区画がないため、同部品の「使わない条件」第1項に該当する |
| Qdrant 起動設定 | 全例外を空検索へ縮退 | 未設定/明示無効のみ縮退、有効だが不正は起動失敗 | **限定縮退 + fail fast** | 既存の「KB 未整備は unmatched」契約を維持しつつ、有効化後の deployment defect を未設定に偽装しない |

## Known Limitations / Out of Scope

- スライド資料（パワポ/Marp）の作成はサポート外（提案書の章立て・ストーリーフロー作成に専念・SRP 維持）。
- `product_kb`/`proposal_kb` への取り込み・整備は本 AG のスコープ外（本 AG は検索・参照のみ）。整備は
  経路(a) 基盤 AG Enterprise Sales Support Data Foundation Builder Agent（任意）または経路(b) 顧客による Data Contract 準拠データの直接用意。
  経路(b) のデータには Enterprise Sales Support Data Foundation Builder Agent の品質検証が掛からないため、品質担保は顧客責務（本 AG のヒット 0 件
  隔離・推測生成禁止が防御線）。
- VectorStore は SDK 1.0.2 の `QdrantVectorStore` に限定。endpoint/model/vector_size 等は
  `config/config.yaml`、埋め込み API key のみ Secret Provider `require()` 経由。
- 最終的な体裁調整・社内承認フロー・顧客への提出はスコープ外（出力はあくまでドラフト）。
- 各営業支援 AG（Enterprise Sales Support Data Foundation Builder Agent / Enterprise Deal Scoring & Pipeline Priority Agent / Enterprise Competitive Analysis & Win Rate Estimation Agent）との実行時連携は持たない（AG 間オーケストレーションは現 FW 非対応。
  疎結合は「共通 RAG を参照」で実現）。
