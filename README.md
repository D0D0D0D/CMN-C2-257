# CMN-C2-257 — Enterprise Sales Proposal Draft Generation Agent

> **Category**: Cat 2 (domain workflow)
> **Industry**: CMN

## Overview

This agent turns the notes a salesperson takes after a customer meeting into a structured
proposal draft in Markdown. It extracts the customer's pain points, searches two knowledge bases —
a product catalogue and a library of past proposals — for material that addresses each one, and
assembles a problem → solution → value → ROI narrative with a source citation on every section.

It suits the gap between a meeting and a first draft: a salesperson who has the notes and needs a
starting document, or a sales organisation that wants every draft to follow the same structure and
carry the same evidence discipline instead of depending on who wrote it.

What the agent refuses to do is the important part:

- **It does not invent product facts.** Specifications are quoted only from retrieved knowledge,
  and each section names the `source_doc` it came from. A pain point that returns no search hit is
  not filled in from the model's own knowledge — it is quarantined and printed in a "該当情報なし・
  要確認" section, so a gap in the knowledge base shows up as a gap in the draft.
- **It screens its own output for misleading claims.** Unsupported superlatives and comparative
  advertising claims are scanned for. A critical finding blocks the response outright; lesser
  findings become a correction list printed at the top of the draft, where a reviewer sees them
  before reading the body.
- **It masks personal data on the way in.** The framework gate masks the patterns it knows; this
  template adds Japanese personal names in meeting-note form (「山田様」「佐藤さん」), which the
  framework detector does not cover, before any of the note reaches a model.
- **An empty draft is an error, not a success.** If generation produces nothing, the invocation
  ends as `status=error` rather than returning a blank document.

The agent does not build or ingest the knowledge bases, does not produce slide decks, and does not
replace the salesperson's review. Its output is a draft.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime;
- a language model — either a `BaseLLM` implementation injected through `config["llm"]`, or the
  bundled default client, which reads `ANTHROPIC_API_KEY` from the platform secret provider
  (never from the process environment). With neither available the run fails rather than
  degrading;
- caller authentication in front of the entry adapter;
- a Qdrant deployment holding your product and proposal collections, if you want retrieval. It is
  **off by default** (`vector_store.enabled: false`), which leaves the documented zero-hit
  behaviour: every pain point lands in the "要確認" section. Enabling it requires every field in
  the block below, plus `SALES_KB_EMBEDDING_API_KEY`; invalid configuration aborts start-up rather
  than running degraded.

Runtime parameters read from `config/config.yaml` (defaults in parentheses):

| Key | Meaning |
|---|---|
| `max_input_chars` (`50000`) | Size ceiling for the meeting notes, enforced at the input gate |
| `top_k` (`5`) | Search hits requested per pain point |
| `min_score` (`0.0`) | Score floor for accepting a hit |
| `contract_version` (`1.0.0`) | The knowledge-base data contract this agent expects |
| `vector_store.enabled` (`false`) | Turns retrieval on; see above |
| `vector_store.product_collection_name` (`product_kb`) / `proposal_collection_name` (`proposal_kb`) | Collections to search |
| `vector_store.embedding_model` (`text-embedding-3-small`) | Embedding model for query vectors |
| `max_retry` (`3`) / `timeout_seconds` (`60`) | Graph backbone tunables |

Documents in both collections must carry the contract metadata — `record_id`, `chunk_hash`,
`source_doc`, `kb_type`, and `product_name` or `proposal_title`. Citations are built from those
fields, so a document missing them cannot be cited and is not usable.

This agent has no within-customer knowledge-base partitions: one deployment, one endpoint, and the
platform's own namespace isolation.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent runs inside the platform runtime and takes its secrets from the platform secret provider. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR, and the agent does not fall back to a degraded
mode when it is absent. Without the framework package installed, `src/api/server.py` cannot be
imported at all — it imports `framework.*` and the SDK's Qdrant store at module level, so start-up
ends in `ImportError` rather than in a partially working server. With the framework present but no
secret provider bound, the default model client cannot be built and the run reports that the key
is not provisioned instead of producing an unsourced draft. The test suite runs without a platform
connection, using injected fakes.

## Input

`POST /invoke` takes a JSON body with `input` and `session_id`. `input` is the meeting or
interview note as plain text.

Callers must be authenticated. A request whose trust level is not stamped by verified upstream
middleware is rejected with **401** unless `INVOKE_AUTH_TOKEN` is set on the server environment
and the request carries the matching `Authorization: Bearer` header, which promotes the caller to
`VERIFIED_EXTERNAL`; a caller below verified trust is rejected with **403**. There is no anonymous
path into the graph.

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "session_id": "demo-1",
    "input": "商談メモ: 棚卸に月3人日かかっており、担当者しか在庫状況が分からない。…"
  }'
```

Notes longer than `max_input_chars` are refused at the input gate before any model call.

## Output

The standard AgentCore envelope is preserved. `output` carries the proposal draft as Markdown;
`result` and `formatted_output` hold the same text, and `compliance_flags` lists the expression
findings the screen raised, each with its section, the expression, the reason and a severity.

The draft opens with an AI-generated-content disclaimer, then — when there is anything to say — a
correction list of flagged expressions and a count of pain points that found no supporting
knowledge, before the proposal body itself. A reviewer therefore reads what needs checking before
reading what was written.

A critical compliance finding does not produce a flagged draft: the output gate blocks it.

## Security and Limitations

Every node requires verified-external trust, so an anonymous caller is stopped before any node
body runs. Meeting notes are size-bounded and PII-masked on the way in — the framework's own
detector plus this template's Japanese-name masking. On the way out, the response is screened for
unsupported comparative claims and personal data; a critical finding blocks the response rather
than annotating it.

Limits worth knowing before you rely on it:

- **Citation proves retrieval, not correctness.** A cited section says the claim came from a
  document in your knowledge base — not that the document is current or right. The draft is only
  as accurate as what you have indexed.
- **The expression screen is a keyword-level aid, not legal review.** It catches shapes of
  claims, not their truth, and it does not substitute for a compliance or legal opinion on
  advertising representations.
- **Coverage gaps are visible but not solved.** A pain point with no hit is reported as
  unresolved. Making it resolvable is a knowledge-base task, not something the agent can do.

Output is machine-generated and assistive. Before anything reaches a customer, a person must
verify the specifications, the numbers and the wording.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2)
- Generation mode: `llm`
- HITL: not enabled
- Persistence: none — no checkpointer; retrieval is read-only and the agent ingests nothing
- Secrets: `ANTHROPIC_API_KEY` for the default model client, `SALES_KB_EMBEDDING_API_KEY` when the
  vector store is enabled
- External API clients: an Anthropic client is bundled as the default; any `BaseLLM` can replace it
  through `config["llm"]`

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection, using injected fakes for both the model and the vector
store. Running the agent itself does not: `src/api/server.py` loads `config/config.yaml`, wires
the Qdrant adapter at start-up when the store is enabled, and takes its keys from the platform
secret provider.

## Documentation

`docs/02_design.md` carries the node flow, the state schema, the knowledge-base data contract and
the security design. `docs/03_test_spec.md` records the test specification and the verification
results, including a live-model run and a platform round-trip.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
