"""Ingest documentation into the vector store.

Pipeline: crawl → adaptive chunk (parent-child) → contextualize → embed → store.
"""

import asyncio

from app.ingestion.crawler import crawl_docs
from app.ingestion.chunker import chunk_document
from app.ingestion.contextualizer import contextualize_chunks
from app.ingestion.embedder import store_parents, embed_and_store_children
from app.db import get_pool, close_pool

SOURCES = {
    "https://docs.anthropic.com": [
        "en/docs/about-claude/models",
        "en/docs/build-with-claude/tool-use",
        "en/docs/build-with-claude/prompt-caching",
        "en/docs/build-with-claude/extended-thinking",
        "en/docs/agents-and-tools/model-context-protocol",
    ],
}


async def main():
    await get_pool()

    total_parents = 0
    total_children = 0

    try:
        for base_url, paths in SOURCES.items():
            print(f"Crawling {base_url}...")
            docs = await crawl_docs(base_url, paths)
            print(f"  Fetched {len(docs)} pages")

            for doc in docs:
                print(f"\n  Processing: {doc.title}")

                result = chunk_document(doc.content, doc.url, doc.title, doc.section)
                doc_type = result.parents[0].document_type if result.parents else "unknown"
                print(f"    Type: {doc_type}")
                print(f"    Parent chunks: {len(result.parents)}")
                print(f"    Child chunks:  {len(result.children)}")

                print(f"    Contextualizing {len(result.children)} chunks...")
                contextualized = await contextualize_chunks(result.children, result.parents)

                print(f"    Storing parents...")
                await store_parents(result.parents)
                total_parents += len(result.parents)

                print(f"    Embedding and storing children...")
                await embed_and_store_children(contextualized)
                total_children += len(contextualized)

        print(f"\nDone.")
        print(f"  Total parent chunks: {total_parents}")
        print(f"  Total child chunks:  {total_children}")

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
