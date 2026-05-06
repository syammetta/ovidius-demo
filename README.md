# Ovidius Doc QA

A production-grade documentation QA agent built on the Anthropic stack. Ingests IRS tax documentation into a Supabase-compatible pgvector store, answers questions with cited sources, and delivers the same retrieval core through three interfaces: a **Claude Managed Agent** (Option A), a **Claude Code Copilot Skill** (Option B), and an **MCP Server** (Option C).

Built in ~6 hours. All three options share a single retrieval pipeline — no logic duplication.

---

## Why IRS Tax Docs?

Most RAG demos use simple documentation with flat structure. Tax law is adversarial for retrieval: answers span multiple publications, rules interact in non-obvious ways (e.g., EITC eligibility depends on filing status, income, and dependents across three different IRS publications), and users ask questions at wildly different complexity levels. If the pipeline works here, it works anywhere.

**Corpus:** 20 IRS publications, 30+ tax topics, and form instructions for the 2025 tax year — ingested, chunked, contextualized, and embedded.

---

## Architecture: 6-Stage Retrieval Pipeline

This is not vanilla RAG. Each stage addresses a specific failure mode:

```
User Query
    │
    ▼
┌─────────────────────────────────┐
│  Stage 0: Query Classification  │  LLM analyzes intent, topics, doc types
│  (Claude Haiku)                 │  → outputs RetrievalStrategy with tuned params
└──────────────┬──────────────────┘
               │
    ┌──────────┴──────────┐
    ▼                     ▼
┌──────────┐      ┌────────────┐
│ Vector   │      │ BM25       │     Two-lane (or three-lane with metadata boost)
│ Search   │      │ Keyword    │     catches both semantic similarity
│ (Voyage) │      │ (tsvector) │     and exact term matches
└────┬─────┘      └─────┬──────┘
     └────────┬─────────┘
              ▼
┌──────────────────────────────┐
│ Stage 1: Reciprocal Rank     │  Rank-based fusion (k=60) — no score
│ Fusion (RRF)                 │  normalization needed
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Stage 2: Cross-Encoder       │  FlashRank (~4MB, CPU, no PyTorch)
│ Reranking                    │  Joint query-doc encoding for precision
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Stage 3: Corrective RAG      │  Claude Haiku judges each chunk:
│                              │  CONFIDENT → proceed
│                              │  UNCERTAIN → filter to relevant only
│                              │  LOW_CONF → transform query & retry
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Stage 4: Parent Chunk        │  Small child chunks (300 tokens) retrieved
│ Expansion                    │  for precision; parent chunks (1500 tokens)
│                              │  expanded for generation context
└──────────────┬───────────────┘
               ▼
┌──────────────────────────────┐
│ Stage 5: Citation-Grounded   │  Claude Sonnet generates from parent context
│ Generation                   │  with inline [1] [2] citations to sources
│                              │  Confidence-aware prompting adjusts behavior
└──────────────────────────────┘
```

**Key technique: Contextual Retrieval.** Before embedding, each child chunk is contextualized by Claude Haiku — generating a 50-100 token prefix explaining where the chunk fits within its parent document. This uses prompt caching (parent document cached, each child chunk is a cheap incremental call). The result: embeddings capture document-level context, not just local content. [Reference](https://www.anthropic.com/news/contextual-retrieval).

---

## Option A: Managed Agent (Multi-Turn Conversation)

**Endpoints:** `POST /agent/chat`, `POST /agent/chat/stream`

A Claude-powered agent with persistent session management and four custom tools:

| Tool | Purpose |
|------|---------|
| `search_knowledge_base` | Full retrieval pipeline (hybrid search + rerank + corrective RAG) |
| `get_document_section` | Fetch broader parent context for a passage found via search |
| `compare_sources` | Multi-query search for side-by-side comparison (e.g., "Roth vs traditional IRA") |
| `calculate_tax` | 2025 tax calculations — standard deductions, bracket math, EITC/CTC phase-outs |

**Session management:** Conversations persist in the database. Messages auto-summarize when the context budget is exceeded (Claude Haiku summarizes older messages, preserving key facts and citations). Extended thinking is optionally available.

**Streaming:** SSE endpoint emits typed events (`session_id`, `thinking`, `tool_call`, `tool_result`, `text_delta`, `done`) so the UI can render agent reasoning in real time.

The agent loop runs up to 10 tool-use turns per request, with a `ToolCache` to skip redundant retrievals within a session.

---

## Option B: Claude Code Copilot Skill

**Location:** `.claude/commands/tax-qa.md` + `app/copilot/adapter.py`

A Claude Code custom slash command that lets users ask tax questions directly from their terminal or IDE:

```bash
# In Claude Code, type:
/project:tax-qa What medical expenses can I deduct?
```

Claude Code invokes the project's QA API, retrieves cited answers from the IRS knowledge base, and returns formatted results with source links — all without leaving the development environment.

Also includes a standalone CLI adapter (`python -m app.copilot.adapter`) for environments without Claude Code, supporting both single-shot and interactive multi-turn sessions with colored terminal output.

**Why this approach:** The brief mentions "Claude Cowork Skill or Plugin" — a Claude Code project command is the closest equivalent, and it demonstrates the same principle: wrapping a specialized knowledge base so it's accessible from the tools people already use.

---

## Option C: MCP Server

**Location:** `app/mcp_server/server.py`

Two tools exposed over stdio transport, compatible with Claude Desktop and any MCP-capable client:

| Tool | Description |
|------|-------------|
| `kb_search` | Retrieval only — returns ranked passages with confidence and source URLs |
| `kb_answer` | Full pipeline — retrieval + citation-grounded generation |

**Claude Desktop config:**
```json
{
  "mcpServers": {
    "ovidius-doc-qa": {
      "command": "python",
      "args": ["-m", "app.mcp_server.server"],
      "cwd": "/path/to/ovidius.ai",
      "env": {
        "ANTHROPIC_API_KEY": "sk-...",
        "VOYAGE_API_KEY": "...",
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```

Both tools call the same retrieval core as the API and agent — same quality guarantees, same observability (queries logged to `query_logs` table with `interface: "mcp"`).

---

## Evaluation

**Dataset:** 15 question-answer pairs stratified across three difficulty tiers.

| Tier | Example | What it tests |
|------|---------|---------------|
| Easy | "What is the standard deduction for 2025?" | Single-source lookup |
| Medium | "How do I calculate the home office deduction?" | Multi-source synthesis |
| Hard | "Should I use AOTC or LLC for college, and can I also deduct loan interest?" | Cross-publication reasoning with interactions |

**Metrics:**

- **RAGAS suite:** Faithfulness, Answer Relevancy, Context Precision, Context Recall
- **Custom pipeline metrics:** Recall@K (expected source URL hit rate), retrieval confidence distribution, per-stage latency breakdown, tier-stratified performance
- **Persistence:** Results stored in `eval_runs` / `eval_results` database tables for historical comparison

```bash
make eval          # CLI runner
POST /eval/run     # API trigger (results stream to dashboard)
```

The evaluation system measures whether the pipeline *actually works*, not just whether it produces output. Corrective RAG confidence routing means the system can distinguish "I found the answer" from "I'm not sure" — and the eval captures that distribution.

---

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL with pgvector extension (or Supabase)
- Anthropic API key
- Voyage AI API key

### Setup

```bash
# Clone and install
git clone <repo-url> && cd ovidius.ai
cp .env.example .env   # Fill in API keys and DATABASE_URL
make install

# Database
make migrate            # Creates tables, indexes, functions

# Ingest IRS corpus
make ingest             # ~50 documents, uses Cloudflare R2 cache if configured

# Run
make serve              # FastAPI on :8000 (dashboard + API)
make mcp                # MCP server (stdio, for Claude Desktop)
make eval               # Run evaluation suite
```

### Dashboard

The dashboard is pre-built in `static/` and served by FastAPI. Access at `http://localhost:8000` with demo code `OVIDIUS-DEMO-2026`.

Five pages: **Ask** (real-time QA with pipeline visualization), **Documents** (browse corpus), **Ingest** (add URLs/files), **Eval** (run and review metrics), **Traces** (OpenTelemetry span browser).

---

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Embeddings | Voyage-3 (1024d) | Top retrieval benchmark performance |
| Vector DB | PostgreSQL + pgvector (IVFFlat) | Supabase-compatible, co-located with BM25 |
| BM25 | PostgreSQL tsvector/tsquery | No extra infra; exact-term recall |
| Reranking | FlashRank | 4MB CPU model, ~95% of LLM reranking at 100x speed |
| Corrective RAG | Claude Haiku | Fast relevance judgments with query transform fallback |
| Contextualization | Claude Haiku + prompt caching | Contextual retrieval at cached-input cost |
| Generation | Claude Sonnet | Citation-grounded answers with confidence-aware prompting |
| Agent tools | Anthropic tool_use API | Native function calling with extended thinking |
| MCP | Official MCP Python SDK | stdio transport for Claude Desktop |
| API | FastAPI + asyncio | Async-first, WebSocket support |
| Frontend | React 19 + Vite + Tailwind | Real-time pipeline visualization |
| Observability | OpenTelemetry | Per-stage tracing, persisted to DB |
| Deployment | Docker + Railway | Two-stage build, optional worker process |

---

## Tradeoffs and What I'd Do Differently

**Made deliberately:**
- **IVFFlat over HNSW:** IVFFlat is simpler and sufficient at demo scale (~5K chunks). Production would use HNSW for better recall at higher cardinality.
- **PostgreSQL tsvector over ParadeDB:** Avoids an extra dependency. tsvector is a BM25 approximation, not true BM25 with term frequency normalization — ParadeDB or Elasticsearch would be the production choice.
- **FlashRank over Cohere Rerank:** Zero API latency, no external dependency. Production would benchmark against Cohere Rerank v3 for absolute precision.
- **Synchronous contextualization:** Each chunk calls Claude serially within a parent group. Production would batch with `asyncio.gather()` and rate limiting.

**With more time:**
- **HyDE (Hypothetical Document Embeddings):** Generate a hypothetical answer, embed that, search with it. Helps when user queries are terse or use different vocabulary than the documents.
- **Query expansion:** Multiple reformulations fused before retrieval, not just on low-confidence retry.
- **Fine-tuned embeddings:** Voyage-3 is strong out of the box, but domain-specific fine-tuning would improve recall on tax terminology.
- **Live evaluation:** Background-score every dashboard query against its retrieved context, building a continuous quality signal beyond the static 15-pair dataset.
- **Streaming generation:** The agent streams via SSE, but the core `/qa` endpoint returns a complete response. WebSocket partially addresses this.

---

## Project Structure

```
app/
├── api/            REST endpoints (QA, ingestion, eval, docs, observability)
├── agent/          Option A: multi-turn agent with tools + streaming
├── mcp_server/     Option C: MCP server (kb_search, kb_answer)
├── copilot/        Option B: CLI adapter + Claude Code slash command
├── retrieval/      6-stage pipeline (classifier, hybrid_search, reranker, corrective, context_builder)
├── generation/     Citation-grounded answer generation
├── ingestion/      Crawler, chunker, contextualizer, embedder, job queue
├── ws/             WebSocket real-time QA
├── middleware/     Query logging
└── telemetry.py    OpenTelemetry instrumentation

eval/               15-pair dataset + RAGAS runner
dashboard/          React + TypeScript + Vite frontend
migrations/         PostgreSQL schema (3 migrations)
scripts/            CLI entry points (ingest, migrate, demo)
docs/               PRD, Architecture, API Spec
```
