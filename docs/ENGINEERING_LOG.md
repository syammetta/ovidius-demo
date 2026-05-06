# Engineering Log

Issues discovered, root causes, and fixes applied to the RAG pipeline. Organized by category so future work can reference what broke and why.

---

## Retrieval Quality

### Vector search only scanning 1% of index
- **Symptom:** Answers frequently said "I don't have enough information" even when relevant docs were ingested.
- **Root cause:** IVFFlat index with `lists=100` defaults to `probes=1`, meaning only 1 out of 100 partitions is searched per query.
- **Fix:** `SET ivfflat.probes = 10` in `db.py:_init_connection`. Now scans 10% of the index.
- **Lesson:** Always set probes explicitly. For small corpora (<10k chunks), consider HNSW or no index at all.

### Voyage AI embeddings missing input_type
- **Symptom:** Retrieval precision lower than expected even with good chunks.
- **Root cause:** `embed_texts()` called Voyage API without `input_type` parameter. Voyage-3 supports asymmetric embedding: `"document"` for indexing, `"query"` for search. Without it, both get generic embeddings in a suboptimal shared space.
- **Fix:** Added `input_type` param to `embed_texts()`, pass `"query"` from vector_store search functions, `"document"` from ingestion. Cache keys now include input_type to prevent cross-contamination.
- **Lesson:** Always check embedding API docs for asymmetric modes. This is a ~3-5% accuracy improvement for free.

### Corrective RAG truncating passages to 400 chars
- **Symptom:** Corrective stage marking relevant chunks as "irrelevant" and filtering them out.
- **Root cause:** Passage text sent for relevance evaluation was truncated to 400 characters — only the first third of a ~300-token chunk. IRS content often has qualifying info (thresholds, exceptions) in the middle or end.
- **Fix:** Increased truncation limit to 1000 characters (covers ~95% of chunk content).
- **Lesson:** When using LLM-as-judge for relevance, show the full content. The marginal token cost (Haiku) is negligible vs. the retrieval quality impact.

### BM25 index includes low-value chunks
- **Status:** Known, not yet fixed.
- **Issue:** Index/glossary chunks and numeric table fragments can rank highly in BM25 because they contain many IRS-specific terms. They pollute the candidate pool before reranking.
- **Potential fix:** Add `is_low_value` column or exclude these from the documents table entirely.

---

## Chunking

### Parent-child linkage broken by chunk overlap
- **Symptom:** Parent expansion (Stage 4) sometimes pulled in unrelated context.
- **Root cause:** `_add_overlap()` prepends text from the previous chunk. Then `_find_parent()` tried substring matching (`child_text in parent_text`), which failed because the overlap-augmented child contains content from a different parent's section.
- **Fix:** Eliminated `_find_parent()` entirely. Chunking strategies now directly return `(text, parent_index)` tuples, tracking lineage during the split rather than reconstructing it afterward.
- **Lesson:** Never try to reconstruct relationships that you already have. Track lineage at creation time.

### No chunk overlap between paragraph boundaries
- **Symptom:** Information spanning two paragraphs was split with zero context carryover.
- **Root cause:** `_split_by_paragraphs()` splits on `\n\n` with no overlap. `_split_by_tokens()` had overlap internally, but paragraph-boundary splits did not.
- **Fix:** Added `_add_overlap()` that prepends ~50 tokens from the previous chunk to each subsequent chunk. Applied after merging small segments in all three chunking strategies.
- **Lesson:** Overlap at every split boundary, not just token-level splits.

### Config chunk_size/chunk_overlap values completely ignored
- **Status:** Known, not yet fixed.
- **Issue:** `settings.chunk_size=600` and `settings.chunk_overlap=100` exist but are almost entirely unused. All values are hardcoded (parent: 1500 tokens, child: 300 tokens, overlap: 50 tokens). The only reference to `settings.chunk_size` is in the API reference overflow check.
- **Impact:** Cannot tune chunk size without editing code. Logged chunk config in telemetry is misleading.

---

## Ingestion

### HTML tables flattened to number streams
- **Symptom:** Tax table chunks from Pub 17 were meaningless sequences like `553\n15,550\n15,600\n1,631`.
- **Root cause:** `BeautifulSoup.get_text()` strips all HTML structure including `<table>`. Column headers ("At least", "But less than", "Single", etc.) get separated from their data.
- **Fix:** Added `_table_to_markdown()` and `_convert_tables_in_place()` in crawler.py. HTML tables are converted to markdown format before text extraction, preserving column relationships.
- **Lesson:** Never use `get_text()` blindly on structured HTML. Convert semantic elements (tables, lists, definition lists) to text-friendly formats first.

### Index/glossary chunks wasting Haiku tokens on contextualization
- **Symptom:** Contextualizer spending 1.5s per chunk on alphabetical term listings and cross-reference pages ("see Armed forces", "see Filing requirements") that have zero retrieval value.
- **Root cause:** No content quality gate before contextualization.
- **Fix:** Added `_is_low_value_content()` heuristic that detects:
  - Cross-reference patterns (`"(see ..."` count >= 3)
  - Comma-heavy short-line content (index listings)
  - Numeric table fragments (digit ratio >= 40%)
  Skipped at both parent level (entire group) and individual child level. Logs savings.
- **Lesson:** Gate expensive per-chunk operations (LLM calls, embedding) with cheap heuristics first.

### Numeric tax table chunks embedded as noise
- **Symptom:** Chunks of raw numbers from flattened tax tables getting contextualized and embedded.
- **Root cause:** Same as table flattening above, plus no filter for numeric-heavy content.
- **Fix:** Extended `_is_low_value_content()` with digit-ratio detection. Combined with the table-to-markdown fix, re-ingested tables now have structure.

### Contextualization stuck at 39% with no progress
- **Symptom:** Ingestion log showed progress up to 39% then froze for minutes.
- **Root cause:** Two issues:
  1. Sequential API calls (~1.5s each x 187 chunks = ~5 min total).
  2. After parallelizing with `asyncio.gather`, progress only reported after the entire parent group finished. If all 187 chunks belonged to one parent, zero updates until completion.
- **Fix:**
  1. Switched to `AsyncAnthropic` with `asyncio.Semaphore(10)` for 10x throughput.
  2. Each task reports progress immediately on completion (not after gather).
  3. Added 30s per-request timeout to prevent indefinite hangs.
  4. Added exponential backoff retry for rate limits (up to 4 retries).
- **Lesson:** For concurrent LLM calls, use per-task progress reporting, not batch-level. Always set timeouts on external API calls.

---

## Pipeline / Architecture

### Sync Anthropic clients blocking async event loop
- **Symptom:** API requests slower than expected; potential request queuing.
- **Root cause:** `classifier.py` and `corrective.py` used synchronous `anthropic.Anthropic` inside async functions, blocking the event loop during LLM calls. `answerer.py` correctly used `asyncio.to_thread()` but the others didn't.
- **Fix:** Switched to `anthropic.AsyncAnthropic` with `await client.messages.create()`.
- **Lesson:** In async code, every blocking I/O call blocks all concurrent requests. Use async clients or `to_thread()`.

### No OpenTelemetry on most pipeline components
- **Symptom:** No visibility into what was slow or failing. Could only see end-to-end latency.
- **Root cause:** OTel instrumentation only existed on the retrieval pipeline orchestrator. Individual components (vector search, BM25, reranker, embedder, crawler, classifier, corrective, contextualizer) had no spans.
- **Fix:** Added spans with timing, token usage, and cache stats to all components. Key attributes: `llm_latency_ms`, `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `query_embed_ms`, `db_query_ms`.
- **Lesson:** Instrument at the component level from day one. Pipeline-level spans hide which stage is the bottleneck.

### Generation mixes parent and child content inconsistently
- **Status:** Known, not yet fixed.
- **Issue:** `answerer.py` uses full parent content for the first child from each parent, but child contextual_content for subsequent children. Citation indices map to children, but the LLM saw parent content for some citations. This causes citation misattribution.
- **Potential fix:** Always use child contextual_content for numbered passages. Provide parent content as optional background context in a separate section.

---

## Testing

### Corrective RAG tests used sync mock after async refactor
- **Symptom:** All corrective tests failing after switching to `AsyncAnthropic`.
- **Root cause:** Tests patched `anthropic.Anthropic` with `MagicMock`. After switching to `AsyncAnthropic`, the mock's `create()` method returned a `MagicMock` (not a coroutine), causing the `await` to fail or the real API to be called.
- **Fix:** Updated mocks to use `AsyncMock` for `client.messages.create`, patched `anthropic.AsyncAnthropic` instead of `anthropic.Anthropic`, added `usage` attribute to mock responses.
- **Lesson:** When switching sync→async in production code, update all test mocks in the same commit.
