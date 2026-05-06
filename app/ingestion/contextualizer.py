"""Anthropic Contextual Retrieval — prepend situating context to each chunk before embedding.

For each child chunk, we use Claude to generate a brief (50-100 token) context
that explains where this chunk fits within the parent document. This dramatically
improves retrieval because the embedding captures document-level context, not just
the chunk's local content.

Uses prompt caching: the parent document is cached once, then each child chunk
generates context referencing the cached parent. This makes the technique cost-effective.

Reference: https://www.anthropic.com/news/contextual-retrieval
"""

import anthropic

from app.config import settings
from app.ingestion.chunker import ParentChunk, ChildChunk

CONTEXT_PROMPT = """<document>
{document}
</document>

Here is the chunk we want to situate within the whole document:
<chunk>
{chunk}
</chunk>

Please give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval of the chunk. Answer only with the context, nothing else."""


async def contextualize_chunks(
    children: list[ChildChunk],
    parents: list[ParentChunk],
) -> list[ChildChunk]:
    """Add contextual prefixes to child chunks using Anthropic's contextual retrieval approach.

    Groups children by parent, caches the parent document, then generates context
    for each child chunk referencing the cached parent.
    """
    parent_map = {p.parent_id: p for p in parents}
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    parent_groups: dict[str, list[ChildChunk]] = {}
    for child in children:
        parent_groups.setdefault(child.parent_id, []).append(child)

    contextualized = []

    for parent_id, group in parent_groups.items():
        parent = parent_map.get(parent_id)
        if not parent:
            for child in group:
                child.content = child.content
            contextualized.extend(group)
            continue

        for child in group:
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"<document>\n{parent.content}\n</document>",
                                "cache_control": {"type": "ephemeral"},
                            },
                            {
                                "type": "text",
                                "text": (
                                    f"Here is the chunk we want to situate within the "
                                    f"whole document:\n<chunk>\n{child.content}\n</chunk>\n\n"
                                    f"Please give a short succinct context to situate this "
                                    f"chunk within the overall document for the purposes of "
                                    f"improving search retrieval of the chunk. Answer only "
                                    f"with the context, nothing else."
                                ),
                            },
                        ],
                    }],
                )
                context = response.content[0].text.strip()
                contextualized_content = f"{context}\n\n{child.content}"
            except Exception:
                contextualized_content = child.content

            updated = ChildChunk(
                chunk_id=child.chunk_id,
                parent_id=child.parent_id,
                content=child.content,
                source_url=child.source_url,
                source_title=child.source_title,
                section=child.section,
                document_type=child.document_type,
                content_hash=child.content_hash,
                token_count=child.token_count,
            )
            updated._contextual_content = contextualized_content
            contextualized.append(updated)

    return contextualized
