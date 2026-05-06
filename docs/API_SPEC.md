# API Specification: Ovidius Doc QA

**Version:** 0.2.0
**Base URL:** `https://<railway-app>.railway.app`

---

## Authentication

None for demo deployment. Production would use API key authentication via `Authorization: Bearer <key>` header.

---

## Endpoints

### POST /qa

Single-turn question answering with citations and pipeline observability.

**Request:**

```json
{
  "question": "How does tool use work in the Claude API?",
  "top_k": 5
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | Yes | — | Natural language question |
| `top_k` | integer | No | 5 | Number of source chunks to use for answer generation |

**Response (200):**

```json
{
  "answer": "Tool use in the Claude API allows you to define custom tools that Claude can invoke during a conversation. You define tools in the `tools` parameter of the Messages API [1], and Claude will return a `tool_use` content block when it decides to call one [2]...",
  "citations": [
    {
      "index": 1,
      "source_url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
      "source_title": "Tool use - Anthropic Docs"
    },
    {
      "index": 2,
      "source_url": "https://docs.anthropic.com/en/docs/build-with-claude/tool-use",
      "source_title": "Tool use - Anthropic Docs"
    }
  ],
  "confidence": "confident",
  "retrieval_method": "hybrid_rrf+rerank",
  "chunks_used": 5,
  "parent_chunks_used": 3,
  "pipeline": [
    {
      "step": "hybrid_search_rerank_correct",
      "duration_ms": 482.3,
      "detail": "confidence=confident, filtered=5/5"
    },
    {
      "step": "generate_answer",
      "duration_ms": 1102.5,
      "detail": "model=claude-sonnet-4-6, parents=3"
    }
  ],
  "total_ms": 1584.8
}
```

| Field | Type | Description |
|-------|------|-------------|
| `answer` | string | Generated answer with inline `[n]` citation markers |
| `citations` | Citation[] | Ordered list of source references |
| `citations[].index` | integer | Matches `[n]` markers in the answer text |
| `citations[].source_url` | string | Canonical URL of the source document |
| `citations[].source_title` | string | Page title for display |
| `confidence` | string | Corrective RAG assessment: `confident`, `uncertain`, or `low_confidence` |
| `retrieval_method` | string | Pipeline stages used (e.g., `hybrid_rrf+rerank`) |
| `chunks_used` | integer | Number of child chunks used for generation |
| `parent_chunks_used` | integer | Number of parent chunks expanded for context |
| `pipeline` | PipelineStep[] | Timing breakdown of each processing stage |
| `pipeline[].step` | string | Stage name |
| `pipeline[].duration_ms` | float | Duration in milliseconds |
| `pipeline[].detail` | string | Stage-specific metadata |
| `total_ms` | float | Total end-to-end latency |

**Error Responses:**

| Status | Condition | Body |
|--------|-----------|------|
| 422 | Missing `question` field | Pydantic validation error |
| 500 | Retrieval or generation failure | `{"detail": "error message"}` |

---

### POST /agent/chat

Multi-turn conversational agent with tool use visibility.

**Request:**

```json
{
  "message": "What is prompt caching and how do I use it?",
  "session_id": null
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `message` | string | Yes | — | User message |
| `session_id` | string | No | null | Session ID for conversation continuity. Omit or null to start a new session. |

**Response (200) — First turn (agent searches):**

```json
{
  "reply": "Prompt caching allows you to cache frequently used context between API calls, reducing latency and cost [1]. To use it, add a `cache_control` block with `type: ephemeral` to the content you want cached [2]...",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "tool_calls": [
    {
      "tool_name": "search_knowledge_base",
      "tool_input": { "query": "prompt caching usage", "top_k": 5 },
      "result_preview": "[1] Source: Prompt caching - Anthropic Docs (https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)\nPrompt caching is a feature that optimizes..."
    }
  ]
}
```

**Response (200) — Follow-up (agent answers from context):**

```json
{
  "reply": "Yes, prompt caching works with tool definitions. Since tool definitions are part of the system prompt, they benefit from caching automatically when you mark them with cache_control [1].",
  "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "tool_calls": []
}
```

| Field | Type | Description |
|-------|------|-------------|
| `reply` | string | Agent's response with inline citations |
| `session_id` | string | Session ID — pass back for follow-up turns |
| `tool_calls` | ToolCallInfo[] | Tools the agent invoked (empty if answered from context) |
| `tool_calls[].tool_name` | string | Tool that was called |
| `tool_calls[].tool_input` | object | Arguments passed to the tool |
| `tool_calls[].result_preview` | string | First 200 chars of tool result |

**Agent tool definitions:**

```json
{
  "name": "search_knowledge_base",
  "description": "Search the documentation knowledge base for passages relevant to a query. Use this when you need to find specific information to answer the user's question.",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "The search query to find relevant documentation passages."
      },
      "top_k": {
        "type": "integer",
        "description": "Number of results to return (default 5).",
        "default": 5
      }
    },
    "required": ["query"]
  }
}
```

---

### GET /health

System health check with database connectivity and corpus size.

**Response (200):**

```json
{
  "status": "ok",
  "child_chunks": 142,
  "parent_chunks": 37,
  "startup_db_error": ""
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"ok"` if all systems operational |
| `child_chunks` | integer | Number of child chunks in `documents` |
| `parent_chunks` | integer | Number of parent chunks in `parent_chunks` |
| `startup_db_error` | string | Startup DB error detail when degraded; empty string when healthy |

---

### GET /

Serves the live dashboard (static HTML). Redirects to `/demo-access` unless demo access cookie is present.

---

### GET /demo-access

Serves the private demo landing page and access code form.

### POST /demo-access

Validates access code and sets an HTTP-only cookie used to access `/` and static dashboard assets.

### POST /demo-logout

Clears the demo access cookie and redirects to `/demo-access`.

---

### POST /api/ingest/url

Queues URL ingestion and returns a durable task id.

**Request:**

```json
{
  "url": "https://www.irs.gov/publications/p17",
  "use_cache": true
}
```

**Response:**

```json
{
  "task_id": "a1b2c3d4",
  "status": "queued"
}
```

### POST /api/ingest/file

Uploads a file and queues ingestion.

**Request:** multipart form with `file`.

**Response:**

```json
{
  "task_id": "a1b2c3d4",
  "status": "queued",
  "filename": "example.pdf"
}
```

### POST /api/ingest/corpus

Queues full corpus ingestion.

**Response:**

```json
{
  "task_id": "a1b2c3d4",
  "status": "queued"
}
```

### GET /api/ingest/tasks

Lists ingestion tasks ordered by newest first.

### GET /api/ingest/tasks/{task_id}

Returns one task with persisted logs and stats.

**Task response shape:**

```json
{
  "task_id": "a1b2c3d4",
  "status": "running",
  "url": "https://www.irs.gov/publications/p17",
  "stats": {
    "parents": 12,
    "children": 76,
    "title": "Publication 17",
    "document_type": "narrative"
  },
  "error": null,
  "logs": [
    "Queued",
    "Worker web-inline picked up job.",
    "Crawling https://www.irs.gov/publications/p17..."
  ]
}
```

---

## MCP Server Tools

The MCP server exposes two tools via stdio transport. Connect from Claude Desktop or any MCP-compatible client.

### kb_search

Search the knowledge base for relevant passages.

**Input schema:**

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "description": "Search query to find relevant documentation."
    },
    "top_k": {
      "type": "integer",
      "description": "Number of results to return (default 5).",
      "default": 5
    }
  },
  "required": ["query"]
}
```

**Output:** Text content with numbered passages, each including source title, URL, and content.

```
[1] Tool use - Anthropic Docs (https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
Tool use allows Claude to interact with external tools and APIs...

[2] Models - Anthropic Docs (https://docs.anthropic.com/en/docs/about-claude/models)
Claude supports tool use across all model variants...
```

### kb_answer

Answer a question using the full QA pipeline (retrieve + rerank + generate).

**Input schema:**

```json
{
  "type": "object",
  "properties": {
    "question": {
      "type": "string",
      "description": "The question to answer from documentation."
    }
  },
  "required": ["question"]
}
```

**Output:** Cited answer followed by source list.

```
Tool use in the Claude API allows you to define custom tools... [1] [2]

Sources:
  [1] Tool use - Anthropic Docs — https://docs.anthropic.com/en/docs/build-with-claude/tool-use
  [2] Models - Anthropic Docs — https://docs.anthropic.com/en/docs/about-claude/models
```

### Claude Desktop Configuration

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ovidius-doc-qa": {
      "command": "python",
      "args": ["-m", "app.mcp_server.server"],
      "cwd": "/path/to/ovidius.ai",
      "env": {
        "ANTHROPIC_API_KEY": "your-key",
        "VOYAGE_API_KEY": "your-key",
        "DATABASE_URL": "your-connection-string"
      }
    }
  }
}
```

---

## Evaluation API (Internal)

The evaluation runner (`make eval`) is a CLI tool, not a REST endpoint. It produces:

**Output file:** `eval/results.json`

```json
{
  "total_pairs": 15,
  "avg_faithfulness": 4.2,
  "avg_recall_at_k": 0.82,
  "results": [
    {
      "id": "easy_01",
      "tier": "easy",
      "question": "What models does Claude offer?",
      "answer": "Claude offers several model families...",
      "retrieval_ms": 312.4,
      "recall_at_k": 1.0,
      "retrieved_urls": ["https://docs.anthropic.com/en/docs/about-claude/models"],
      "faithfulness_score": 5,
      "faithfulness_reason": "All claims directly supported by source passage."
    }
  ]
}
```

The dashboard reads these results and displays them in the eval metrics panel. Additionally, every query made through the dashboard is background-scored and added to the running metrics.

---

## Rate Limits and Performance Targets

| Metric | Target | Notes |
|--------|--------|-------|
| QA endpoint latency (p95) | < 3s | Dominated by Anthropic API call time |
| Retrieval latency (p95) | < 500ms | Embed + vector search + rerank |
| Generation latency (p95) | < 2s | Sonnet generation |
| Concurrent queries | 10+ | asyncpg connection pool: 2-10 connections |
| Embedding batch size | 50 texts/call | Voyage API limit per request |

---

## Error Handling

All endpoints return standard HTTP status codes:

| Code | Meaning |
|------|---------|
| 200 | Success |
| 422 | Validation error (missing/invalid fields) |
| 500 | Internal error (DB, API failure) |

Error bodies follow FastAPI's default format:

```json
{
  "detail": "Description of what went wrong"
}
```
