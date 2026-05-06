"""MCP server exposing documentation knowledge base as tools.

Two tools mirror the internal architecture:
- kb_search: retrieval only (hybrid search + rerank + corrective)
- kb_answer: full pipeline (retrieve + generate with citations)

Both use the shared retrieval core — same quality guarantees as the API.
"""

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from app.retrieval.context_builder import retrieve
from app.generation.answerer import generate_answer
from app.telemetry import get_tracer, get_current_trace_id
from app.middleware.query_logger import log_query, QueryLogEntry

server = Server("ovidius-doc-qa")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="kb_search",
            description=(
                "Search the documentation knowledge base for relevant passages. "
                "Uses hybrid retrieval (vector + BM25), cross-encoder reranking, "
                "and corrective evaluation. Returns ranked passages with source URLs "
                "and retrieval confidence."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to find relevant documentation.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="kb_answer",
            description=(
                "Answer a question using the documentation knowledge base. "
                "Runs the full pipeline: hybrid search, reranking, corrective evaluation, "
                "parent chunk expansion, and citation-grounded generation. "
                "Returns a cited answer with source references and confidence level."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to answer from documentation.",
                    },
                },
                "required": ["question"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    tracer = get_tracer("mcp")

    if name == "kb_search":
        with tracer.start_as_current_span("mcp_kb_search") as span:
            span.set_attribute("query", arguments["query"][:500])
            span.set_attribute("interface", "mcp")

            retrieval_result = await retrieve(
                arguments["query"],
                top_k=arguments.get("top_k", 5),
            )

            results = []
            confidence = retrieval_result.corrective.confidence.value
            results.append(f"Retrieval confidence: {confidence}")
            results.append(
                f"Filtered: {retrieval_result.corrective.filtered_count}"
                f"/{retrieval_result.corrective.original_count} chunks relevant"
            )
            results.append("")

            for i, c in enumerate(retrieval_result.children):
                parent_text = retrieval_result.parent_contents.get(c.parent_id, "")
                content = parent_text if parent_text else (c.contextual_content or c.content)
                results.append(
                    f"[{i + 1}] {c.source_title} ({c.source_url})\n"
                    f"    Type: {c.document_type} | Method: {c.retrieval_method}\n"
                    f"{content}"
                )

            text = "\n\n".join(results) if results else "No relevant passages found."
            return [TextContent(type="text", text=text)]

    elif name == "kb_answer":
        with tracer.start_as_current_span("mcp_kb_answer") as span:
            span.set_attribute("question", arguments["question"][:500])
            span.set_attribute("interface", "mcp")

            retrieval_result = await retrieve(arguments["question"])
            result = await generate_answer(arguments["question"], retrieval_result)

            citations = "\n".join(
                f"  [{c.index}] {c.source_title} — {c.source_url}"
                for c in result.citations
            )
            confidence_note = (
                f"\n\nRetrieval confidence: {result.confidence}"
                f" | Method: {result.retrieval_method}"
                f" | Chunks: {result.chunks_used} (parents: {result.parent_chunks_used})"
            )
            text = f"{result.answer}\n\nSources:\n{citations}{confidence_note}"

            try:
                await log_query(QueryLogEntry(
                    question=arguments["question"],
                    answer=result.answer[:1000],
                    confidence=result.confidence,
                    retrieval_method=result.retrieval_method,
                    chunks_used=result.chunks_used,
                    parent_chunks_used=result.parent_chunks_used,
                    trace_id=get_current_trace_id(),
                    interface="mcp",
                ))
            except Exception:
                pass

            return [TextContent(type="text", text=text)]

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
