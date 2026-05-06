# Architecture Document: Ovidius Doc QA

**Version:** 0.2.0
**Date:** 2026-05-06

---

## 1. Design Principles

1. **No one-size-fits-all.** Different documents need different chunking strategies. API references aren't narrative guides aren't code examples. The pipeline adapts to content type.

2. **Retrieval quality compounds.** Every stage (hybrid search, reranking, corrective evaluation) adds measurable quality — and each is independently benchmarkable. Stack good decisions, get good results.

3. **Small chunks for retrieval, large chunks for generation.** Parent-child architecture resolves the fundamental precision-vs-context tradeoff. Small child chunks produce precise embeddings; parent chunks give the LLM enough context to generate substantive answers.

4. **Trust but verify.** Corrective RAG evaluates retrieval quality before generation. If the system isn't confident, it says so rather than hallucinating from irrelevant context.

5. **Single retrieval core, multiple surfaces.** Every interface (API, agent, MCP, copilot) calls the same pipeline. No logic duplication.

## 2. Retrieval Pipeline Deep Dive

This is not a vanilla RAG. The pipeline stacks six techniques, each addressing a specific failure mode:

```
User Query
    │
    ├──────────────────────────────────────────────────────────────┐
    │                                                              │
    ▼                                                              ▼
┌─────────────────┐                                    ┌──────────────────┐
│ Vector Search    │                                    │ BM25 Keyword     │
│ (Voyage-3 embed  │                                    │ Search            │
│  + pgvector      │                                    │ (tsvector/tsquery │
│  cosine dist)    │                                    │  full-text rank)  │
│                  │                                    │                   │
│ Catches:         │                                    │ Catches:          │
│ semantic sim     │                                    │ exact terms       │
│ "car"↔"auto"    │                                    │ "claude-sonnet"   │
└────────┬────────┘                                    └────────┬──────────┘
         │                                                      │
         └──────────────────┬───────────────────────────────────┘
                            │
                            ▼
              ┌──────────────────────────┐
              │ Reciprocal Rank Fusion   │
              │ (RRF, k=60)             │
              │                          │
              │ Rank-based fusion —      │
              │ no score normalization   │
              │ needed. Robust to        │
              │ distribution differences │
              └────────────┬─────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │ Cross-Encoder Reranking  │
              │ (FlashRank, ~4MB, CPU)   │
              │                          │
              │ Joint query-document     │
              │ encoding captures        │
              │ fine-grained relevance   │
              │ that bi-encoders miss    │
              └────────────┬─────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │ Corrective RAG           │
              │ (Haiku relevance judge)  │
              │                          │
              │ CONFIDENT → proceed      │
              │ UNCERTAIN → filter       │
              │ LOW_CONF  → retry with   │
              │   transformed query,     │
              │   or acknowledge gap     │
              └────────────┬─────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │ Parent Chunk Expansion   │
              │                          │
              │ Retrieved child chunks   │
              │ are precise but small.   │
              │ Fetch parent chunks for  │
              │ generation context.      │
              └────────────┬─────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │ Citation-Grounded        │
              │ Generation (Sonnet)      │
              │                          │
              │ Confidence-aware prompt: │
              │ LOW_CONF → extra caution │
              │ uses parent context but  │
              │ cites child sources      │
              └──────────────────────────┘
```

### Why each stage exists

| Stage | Failure mode it addresses | Without it |
|-------|--------------------------|------------|
| Hybrid search (BM25 + vector) | Pure vector misses exact terms; pure keyword misses synonyms | "claude-sonnet-4-6" retrieves general model docs instead of the specific model page |
| RRF fusion | Different score distributions from vector vs keyword | Can't combine results without normalizing — RRF avoids this entirely |
| Cross-encoder rerank | Bi-encoder similarity is coarse — "about the topic" ≠ "answers the question" | Top-5 chunks are topically related but don't contain the actual answer |
| Corrective RAG | Blind trust in retrieval → confident wrong answers | LLM generates from irrelevant context, producing plausible hallucinations |
| Parent expansion | Small chunks lack surrounding context for generation | Answer is technically correct but misses nuance from adjacent paragraphs |
| Confidence-aware generation | Same prompt regardless of retrieval quality | System never says "I don't know" — always generates something, even from garbage |

## 3. Ingestion Pipeline Deep Dive

### 3.1 Adaptive Chunking

Documents are classified by type, and each type gets a purpose-built chunking strategy:

```
Document
    │
    ▼
┌──────────────────────┐
│ Type Detection        │
│ (URL patterns +       │
│  content heuristics)  │
└──────────┬───────────┘
           │
     ┌─────┼──────────────────┐
     │     │                  │
     ▼     ▼                  ▼
┌────────┐ ┌─────────┐ ┌──────────┐
│API Ref │ │Narrative│ │Code Heavy│
│        │ │Guide    │ │          │
│Split by│ │Split by │ │Preserve  │
│endpoint│ │heading +│ │code      │
│/method │ │paragraph│ │blocks as │
│boundary│ │boundary │ │atomic    │
│        │ │         │ │units     │
│Child:  │ │Child:   │ │Child:    │
│~200tok │ │~300tok  │ │~300tok   │
│param/  │ │paragraph│ │code +    │
│example │ │groups   │ │adjacent  │
│        │ │         │ │prose     │
│Parent: │ │Parent:  │ │Parent:   │
│full    │ │full     │ │full      │
│endpoint│ │section  │ │section   │
│~1500tok│ │~1500tok │ │~1500tok  │
└────────┘ └─────────┘ └──────────┘
```

**Why not one-size-fits-all chunking:**

- **API references** have dense, structured content. A 600-token chunk might split a parameter list in half, losing the mapping between parameter name and description. Splitting by endpoint boundary preserves the complete parameter set.
- **Narrative guides** have flowing prose where paragraph boundaries are natural semantic breaks. Fixed-token splitting cuts mid-sentence, creating chunks that start with orphaned fragments.
- **Code examples** are atomic — a function split across two chunks is useless in both. Code blocks are preserved whole, with their surrounding explanation attached.

### 3.2 Contextual Retrieval (Anthropic's Technique)

After chunking, each child chunk gets a contextual prefix generated by Claude:

```
Before (raw chunk):
  "Its population is 3.7 million and growing, making it
   one of the fastest-growing cities in the region."

After (contextualized chunk):
  "This chunk is from the Berlin city overview section,
   discussing demographic trends in German cities.
   Its population is 3.7 million and growing, making it
   one of the fastest-growing cities in the region."
```

**Implementation uses prompt caching:** the parent document is sent once with `cache_control: ephemeral`, then each child chunk generates context referencing the cached parent. Without caching, we'd re-send the full parent for every child — prohibitively expensive.

**Impact:** Anthropic's research shows contextual embeddings reduce retrieval failure rate by 35% standalone, and 67% combined with BM25 and reranking. We use all three.

### 3.3 Parent-Child Architecture

```
┌─────────────────────────────────────────────────┐
│                 Parent Chunk                      │
│   (full section, ~1500 tokens)                    │
│                                                   │
│   Used for GENERATION — gives LLM enough          │
│   context to produce substantive answers           │
│                                                   │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│   │  Child 1  │  │  Child 2  │  │  Child 3  │     │
│   │ ~200-300  │  │ ~200-300  │  │ ~200-300  │     │
│   │ tokens    │  │ tokens    │  │ tokens    │     │
│   │           │  │           │  │           │     │
│   │ Used for  │  │ Used for  │  │ Used for  │     │
│   │ RETRIEVAL │  │ RETRIEVAL │  │ RETRIEVAL │     │
│   │           │  │           │  │           │     │
│   │ Embedded  │  │ Embedded  │  │ Embedded  │     │
│   │ with      │  │ with      │  │ with      │     │
│   │ contextual│  │ contextual│  │ contextual│     │
│   │ prefix    │  │ prefix    │  │ prefix    │     │
│   └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────┘
```

**The fundamental tradeoff:** small chunks produce focused embeddings (precise retrieval) but lack context (poor generation). Large chunks provide context but produce noisy embeddings (imprecise retrieval). Parent-child resolves this: retrieve on children, generate from parents.

## 4. Evaluation Architecture

### RAGAS Metrics

| Metric | What it measures | How |
|--------|-----------------|-----|
| **Faithfulness** | Are claims in the answer supported by context? | LLM extracts claims, checks each against retrieved passages |
| **Answer Relevancy** | Is the answer pertinent to the question? | Generate hypothetical questions from answer, measure cosine similarity to original |
| **Context Precision** | Are relevant chunks ranked higher? | Check if signal appears at top of retrieved list |
| **Context Recall** | Did retrieval find all needed information? | Compare retrieved content against ground truth |

### Custom Pipeline Metrics

| Metric | What it measures |
|--------|-----------------|
| **Recall@K** | Did we retrieve the expected source URLs? |
| **Retrieval Confidence** | Corrective RAG's self-assessment distribution |
| **Latency Breakdown** | Per-stage timing (retrieval vs generation) |
| **Tier Performance** | Quality stratified by question difficulty |

### Live Evaluation

Every query through the dashboard is background-scored. The eval panel shows running averages, not just batch results. This makes evaluation a continuous signal, not a one-time test.

## 5. System Context

```
Railway Project (same network)
├─────────────────────────────────────────────────────┐
│                                                      │
│  ┌──────────────────────┐    ┌─────────────────────┐│
│  │   FastAPI Service     │    │  Postgres + pgvector ││
│  │                       │◄──►│                     ││
│  │  ┌─ POST /qa         │    │  ┌─ parent_chunks   ││
│  │  ├─ POST /agent/chat │    │  ├─ documents (child ││
│  │  ├─ GET /health      │    │  │   + embeddings    ││
│  │  ├─ GET / (dashboard)│    │  │   + tsvector)     ││
│  │  └─ WS /ws/trace     │    │  └─ sessions        ││
│  └───────────┬──────────┘    └─────────────────────┘│
│              │                                       │
└──────────────┼───────────────────────────────────────┘
               │
    ┌──────────┼──────────────────────┐
    │          │                      │
┌───▼───┐ ┌───▼────┐ ┌──────────────▼─┐
│Browser │ │Copilot │ │ Claude Desktop  │
│(Dash)  │ │CLI     │ │ (MCP Client)   │
└────────┘ └────────┘ └────────────────┘

External APIs:
  ┌──────────────┐  ┌──────────┐
  │ Anthropic     │  │ Voyage   │
  │ Claude Sonnet │  │ AI       │
  │ (generation)  │  │ (embed)  │
  │ Claude Haiku  │  │          │
  │ (context,     │  │          │
  │  rerank,      │  │          │
  │  corrective)  │  │          │
  └───────────────┘  └──────────┘
```

## 6. Data Model

### parent_chunks

```sql
parent_id     TEXT PRIMARY KEY    -- hash-based
content       TEXT NOT NULL       -- full section text (~1500 tokens)
source_url    TEXT NOT NULL       -- canonical URL
source_title  TEXT NOT NULL       -- page title
section       TEXT DEFAULT ''     -- doc section path
document_type TEXT DEFAULT 'narrative'  -- api_reference | narrative | code_heavy
token_count   INTEGER NOT NULL
created_at    TIMESTAMPTZ
```

### documents (child chunks)

```sql
chunk_id             TEXT PRIMARY KEY    -- hash-based
parent_id            TEXT → parent_chunks(parent_id)
content              TEXT NOT NULL       -- raw child chunk text
contextual_content   TEXT               -- with Anthropic contextual prefix
source_url           TEXT NOT NULL
source_title         TEXT NOT NULL
section              TEXT DEFAULT ''
document_type        TEXT DEFAULT 'narrative'
content_hash         TEXT NOT NULL       -- dedup
token_count          INTEGER NOT NULL
embedding            vector(1024)       -- Voyage-3, contextual content embedded
tsv                  tsvector           -- auto-populated for BM25 search
created_at           TIMESTAMPTZ
```

**Indexes:**
- IVFFlat on `embedding` (cosine distance, 100 lists — appropriate for demo scale)
- GIN on `tsv` for full-text search
- B-tree on `parent_id` for parent expansion lookups
- B-tree on `content_hash` for dedup checks

**Production upgrades:** HNSW index for embedding (better recall at scale), ParadeDB `pg_search` for true BM25 scoring (vs Postgres ts_rank_cd approximation).

## 7. Key Technical Decisions

| Decision | Chose | Over | Rationale |
|----------|-------|------|-----------|
| Search strategy | Hybrid (vector + BM25 + RRF) | Vector-only | 8-15% accuracy improvement; catches exact-term queries that vector search misses |
| Reranker | FlashRank cross-encoder | LLM-as-reranker (Haiku) | ~100x faster, no API cost, ~95% of LLM accuracy. CPU-only, 4MB model. |
| Retrieval quality | Corrective RAG | Blind trust | Prevents confident wrong answers from irrelevant context. Adds ~200ms but worth it. |
| Chunk architecture | Parent-child | Flat fixed-size | Resolves precision-vs-context tradeoff. Small retrieval, large generation. |
| Chunking strategy | Adaptive by doc type | One-size-fits-all | API refs, narrative guides, and code examples have fundamentally different structure |
| Context enrichment | Anthropic contextual retrieval | Raw chunk embedding | 35-67% retrieval failure reduction per Anthropic's research |
| Evaluation | RAGAS + custom pipeline metrics | Homegrown scoring | Industry standard, multiple validated metrics, comparable benchmarks |
| Embeddings | Voyage-3 (1024d) | OpenAI, Cohere | Anthropic-recommended, strong retrieval benchmarks |
| BM25 implementation | Postgres tsvector/tsquery | ParadeDB pg_search | Built-in, no extension install needed. Note: ParadeDB for production. |
| Vector store | Postgres pgvector (Railway) | Pinecone, Supabase | Same-network, SQL-native, no vendor lock-in |

## 8. What We'd Add Next

**Tier 1 — Immediate production improvements:**
- ColBERT v2 (via RAGatouille) for late-interaction retrieval alongside dense embeddings
- ParadeDB `pg_search` for true BM25 scoring instead of ts_rank_cd
- HNSW index for better recall at scale

**Tier 2 — Architecture extensions:**
- LightRAG for knowledge graph augmented retrieval (entity-relationship queries)
- RAPTOR for hierarchical summarization (broad thematic queries)
- HyDE as a fallback for queries where both vector + BM25 fail
- Query decomposition for complex multi-part questions

**Tier 3 — Production infrastructure:**
- Auth + API keys, multi-tenant client isolation
- Langfuse / OpenTelemetry for production observability
- Feedback loop: thumbs up/down → retrieval weight tuning
- Incremental re-ingestion via doc site webhooks
- Rate limiting + query embedding cache for hot queries
