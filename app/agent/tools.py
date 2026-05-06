"""Custom tools for the managed agent."""

from app.retrieval.context_builder import retrieve
from app.retrieval.vector_store import RetrievedChunk

TOOL_DEFINITIONS = [
    {
        "name": "search_knowledge_base",
        "description": (
            "Search the documentation knowledge base for passages relevant to a query. "
            "Uses hybrid search (vector + keyword), cross-encoder reranking, and "
            "corrective evaluation to find the most relevant passages. "
            "Use this when you need to find specific information to answer the user's question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant documentation passages.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
]


async def handle_tool_call(name: str, input_data: dict) -> str:
    """Execute a tool call and return formatted results."""
    if name == "search_knowledge_base":
        retrieval_result = await retrieve(
            input_data["query"],
            top_k=input_data.get("top_k", 5),
        )

        children = retrieval_result.children
        parent_contents = retrieval_result.parent_contents
        confidence = retrieval_result.corrective.confidence.value

        results = []
        for i, c in enumerate(children):
            parent_text = parent_contents.get(c.parent_id, "")
            content = parent_text if parent_text else (c.contextual_content or c.content)
            results.append(
                f"[{i + 1}] Source: {c.source_title} ({c.source_url})\n"
                f"Type: {c.document_type} | Retrieval: {c.retrieval_method}\n"
                f"{content}"
            )

        header = f"Retrieval confidence: {confidence}\n\n"
        body = "\n\n".join(results) if results else "No relevant passages found."
        return header + body

    return f"Unknown tool: {name}"
