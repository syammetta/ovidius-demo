# Product Requirements Document: Ovidius Doc QA

**Version:** 0.1.0
**Author:** Syam Metta
**Date:** 2026-05-06
**Status:** Implementation

---

## 1. Problem Statement

Engineering teams building on the Anthropic platform spend significant time searching across fragmented documentation (API references, guides, MCP specs, SDK docs) to answer implementation questions. The answers often require synthesizing information from multiple pages, and there's no way to verify whether the information found is complete or current.

This is a microcosm of the exact problem Ovidius solves for its clients: **turning scattered knowledge into reliable, cited, verifiable answers delivered through the interfaces people already use.**

## 2. Product Vision

Build a production-grade documentation QA system that demonstrates three core competencies:

1. **Retrieval quality over retrieval quantity** — getting the right 5 chunks out of thousands matters more than having thousands of chunks
2. **Multi-surface delivery from a single core** — the same retrieval+generation pipeline serves an API, a conversational agent, and an MCP tool server, proving the architecture is composable
3. **Observable, measurable AI** — every query produces visible pipeline telemetry, and answer quality is continuously evaluated, not assumed

## 3. Target Users

| User | Need | Interface |
|------|------|-----------|
| Developer (API consumer) | Programmatic access to doc QA | `POST /qa` REST endpoint |
| Developer (conversational) | Multi-turn Q&A with follow-ups | Agent chat via `POST /agent/chat` |
| Claude Desktop user | QA from within their AI workflow | MCP server tools |
| Non-technical team member | Quick answers without setup | Copilot CLI adapter |
| Evaluator (Ovidius reviewer) | See system quality + architecture | Live dashboard with pipeline trace + eval metrics |

## 4. Functional Requirements

### 4.1 Document Ingestion

| ID | Requirement | Priority |
|----|-------------|----------|
| ING-1 | Crawl public documentation sites given a base URL + path list | P0 |
| ING-2 | Extract clean text content from HTML, preserving structural hierarchy | P0 |
| ING-3 | **Adaptive chunking**: classify document type (API ref, narrative, code-heavy) and apply type-specific chunking strategy | P0 |
| ING-4 | **Parent-child architecture**: large parent chunks for generation context, small child chunks for precise retrieval | P0 |
| ING-5 | **Contextual retrieval** (Anthropic's technique): prepend LLM-generated situating context to each child chunk before embedding | P0 |
| ING-6 | Generate embeddings via Voyage AI on contextualized content and store in Postgres pgvector | P0 |
| ING-7 | Deduplicate chunks via content hashing (idempotent upsert) | P0 |
| ING-8 | Preserve metadata: source URL, title, section, document type, parent-child linkage, token count | P0 |
| ING-9 | Use Anthropic prompt caching during contextualization for cost efficiency | P0 |
| ING-10 | Support incremental re-ingestion without full re-index | P1 |

### 4.2 Retrieval

| ID | Requirement | Priority |
|----|-------------|----------|
| RET-1 | **Hybrid search**: vector similarity (pgvector) + BM25 keyword search (tsvector) executed in parallel | P0 |
| RET-2 | **Reciprocal Rank Fusion**: merge vector and BM25 results using rank-based fusion (k=60) | P0 |
| RET-3 | **Cross-encoder reranking**: FlashRank reranker for precision (replaces LLM reranker) | P0 |
| RET-4 | **Corrective RAG**: evaluate retrieval confidence per-chunk, route as confident/uncertain/low-confidence | P0 |
| RET-5 | **Query transformation**: on low-confidence retrieval, transform query and retry | P0 |
| RET-6 | **Parent chunk expansion**: after retrieving child chunks, fetch parent chunks for generation | P0 |
| RET-7 | Return retrieval method, confidence level, and filtering stats in response | P0 |
| RET-8 | Configurable top-N and top-K parameters | P1 |

### 4.3 Answer Generation

| ID | Requirement | Priority |
|----|-------------|----------|
| GEN-1 | Generate answers grounded exclusively in retrieved context (parent chunks) | P0 |
| GEN-2 | Inline citation markers ([1], [2]) mapped to child chunk source URLs | P0 |
| GEN-3 | **Confidence-aware prompting**: low-confidence retrieval triggers cautious generation with explicit uncertainty acknowledgment | P0 |
| GEN-4 | Deterministic citation-to-source mapping (no hallucinated references) | P0 |
| GEN-5 | Response includes confidence level, retrieval method, chunks used, parents used | P0 |

### 4.4 API Endpoint (Base Requirement)

| ID | Requirement | Priority |
|----|-------------|----------|
| API-1 | `POST /qa` accepting question + optional top_k | P0 |
| API-2 | Response includes answer, citations[], pipeline timing, total latency | P0 |
| API-3 | `GET /health` with document count and DB connectivity check | P0 |
| API-4 | CORS configured for dashboard frontend | P0 |

### 4.5 Managed Agent — Option A

| ID | Requirement | Priority |
|----|-------------|----------|
| AGT-1 | Multi-turn conversation with persistent session state (Postgres-backed) | P0 |
| AGT-2 | Custom `search_knowledge_base` tool the agent invokes autonomously | P0 |
| AGT-3 | Agent decides when to search vs answer from conversation context | P0 |
| AGT-4 | Tool call metadata exposed in response (name, input, result preview) | P0 |
| AGT-5 | Session history bounded to prevent stale context contamination | P1 |

### 4.6 MCP Server — Option C

| ID | Requirement | Priority |
|----|-------------|----------|
| MCP-1 | `kb_search` tool: vector search with formatted passage results | P0 |
| MCP-2 | `kb_answer` tool: full QA pipeline returning cited answer | P0 |
| MCP-3 | Strict JSON input/output schemas for both tools | P0 |
| MCP-4 | Runs via stdio transport, connectable from Claude Desktop | P0 |
| MCP-5 | README includes Claude Desktop config snippet | P0 |

### 4.7 Copilot CLI — Option B

| ID | Requirement | Priority |
|----|-------------|----------|
| COP-1 | CLI command that forwards question to QA endpoint | P0 |
| COP-2 | Formatted terminal output with answer + clickable citation URLs | P0 |
| COP-3 | Interactive mode (prompt loop) and single-shot mode | P1 |

### 4.8 Evaluation

| ID | Requirement | Priority |
|----|-------------|----------|
| EVL-1 | 15 QA pairs across easy/medium/hard tiers | P0 |
| EVL-2 | **RAGAS metrics**: faithfulness, answer relevancy, context precision, context recall | P0 |
| EVL-4 | Custom retrieval metric: Recall@K against expected source URLs | P0 |
| EVL-5 | Corrective RAG confidence distribution tracking | P0 |
| EVL-6 | Per-stage latency breakdown (retrieval vs generation) | P0 |
| EVL-7 | Single runner script producing JSON results | P0 |
| EVL-8 | Live evaluation: every dashboard query gets background-scored | P1 |
| EVL-9 | Per-tier performance breakdown (easy/medium/hard) | P1 |

### 4.9 Live Dashboard

| ID | Requirement | Priority |
|----|-------------|----------|
| DSH-1 | Single-page UI showing conversation, pipeline trace, eval metrics, query log | P0 |
| DSH-2 | Pipeline trace animates per-step as query executes (via WebSocket or SSE) | P0 |
| DSH-3 | Citations are clickable links to source documentation | P0 |
| DSH-4 | Agent tool calls visible as badges/expansions in conversation | P0 |
| DSH-5 | Eval metrics panel: running averages for faithfulness and recall | P0 |
| DSH-6 | Query log: accumulating history with per-query scores and latency | P0 |
| DSH-7 | System status indicators: API health, DB connectivity, doc count | P1 |

## 5. Non-Functional Requirements

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1 | End-to-end QA latency | < 3s (p95) |
| NFR-2 | Retrieval latency (embed + search + rerank) | < 1.5s (p95) |
| NFR-3 | Concurrent user support | 10+ simultaneous queries |
| NFR-4 | Database connection pooling | 2-10 connections |
| NFR-5 | Zero external dependencies beyond Railway + Anthropic + Voyage APIs | Required |
| NFR-6 | Deployable via single `railway up` | Required |

## 6. Documentation Corpus

**Primary corpus:** Anthropic documentation

Selected for relevance to the role (building on the Anthropic stack) and diversity of question types:

| Section | Content Type | Question Complexity |
|---------|-------------|-------------------|
| Models overview | Factual reference | Easy — direct lookups |
| Tool use guide | Implementation guide | Medium — multi-step procedures |
| Prompt caching | Feature guide with constraints | Medium — conditional logic |
| Extended thinking | Advanced feature | Hard — architectural tradeoffs |
| MCP overview | Protocol specification | Hard — comparative/design questions |

This corpus is intentionally narrow. A small, well-chosen corpus makes retrieval quality more visible — you can't hide behind volume.

## 7. Success Criteria

| Criterion | Measurement | Target |
|-----------|------------|--------|
| Retrieval relevance | Recall@5 on eval dataset | > 0.75 |
| Answer faithfulness | LLM-as-judge mean score | > 3.5/5 |
| Citation accuracy | Manual spot-check (5 queries) | 100% valid links |
| Multi-turn coherence | Agent uses context vs re-searching appropriately | Qualitative |
| System observability | Every query shows pipeline timing on dashboard | 100% |
| Deployment | Live URL accessible by reviewers | Required |

## 8. Out of Scope (With Rationale)

| Feature | Why deferred |
|---------|-------------|
| Authentication / API keys | Demo system — adds setup friction, no security benefit for public docs |
| Multi-tenancy | Single-corpus demo — architecture supports it but implementing it is premature |
| Rate limiting | Low-traffic demo — note in README as production requirement |
| Streaming responses | Adds complexity to citation tracking — would implement via SSE in production |
| Fine-tuned embeddings | Voyage-3 is strong baseline — would benchmark against fine-tuned if retrieval metrics underperform |
| Hybrid search (BM25) | pgvector alone should handle the corpus scale — noted as P2 for exact-match edge cases |

## 9. Technical Constraints

- **Python 3.11+** — explicitly requested in the brief
- **FastAPI** — explicitly requested in the brief
- **Postgres + pgvector** — production-standard vector store, same-network deployment on Railway
- **Anthropic SDK** — generation and agent orchestration
- **Voyage AI** — embeddings (Anthropic-recommended provider)
- **No frontend framework** — vanilla HTML/CSS/JS served as static files, minimizes build complexity

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Anthropic docs may block crawling | No corpus to index | Pre-download content, note in README; provide fallback corpus option |
| Voyage API latency on embedding | Slow ingestion | Batch embeddings, cache query embeddings for repeated eval runs |
| LLM reranker adds latency | Slower retrieval | Use Haiku for reranking (fast + cheap), benchmark against vector-only to prove the tradeoff |
| Dashboard complexity creep | Delays core delivery | Dashboard is Phase 2 — core pipeline + eval ship first |
| Railway pgvector support | Can't deploy | Railway Postgres supports pgvector extension — verified. Fallback: Supabase. |
