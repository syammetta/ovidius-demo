"""Ingestion worker runtime for processing queued ingestion jobs."""

from __future__ import annotations

import asyncio
import io
import logging
import socket
from typing import Any

from app.config import settings
from app.ingestion.chunker import chunk_document
from app.ingestion.contextualizer import contextualize_chunks
from app.ingestion.crawler import crawl_docs, crawl_url, crawl_urls
from app.ingestion.embedder import embed_and_store_children, store_parents
from app.ingestion.job_queue import (
    append_job_log,
    claim_job_by_id,
    claim_next_job,
    complete_job,
    fail_job,
    get_job,
    pop_job_id,
    update_job_progress,
)

logger = logging.getLogger(__name__)


def _extract_text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


async def _process_document(
    job_id: str,
    content: str,
    source_url: str,
    source_title: str,
    section: str = "",
) -> dict[str, Any]:
    result = chunk_document(content, source_url, source_title, section)
    doc_type = result.parents[0].document_type if result.parents else "unknown"
    await append_job_log(
        job_id,
        f"Chunked: {len(result.parents)} parents, {len(result.children)} children ({doc_type})",
    )

    if not result.children:
        await append_job_log(job_id, "No chunks produced - skipping.")
        return {"parents": 0, "children": 0, "title": source_title, "document_type": doc_type}

    await append_job_log(job_id, f"Contextualizing {len(result.children)} chunks...")
    contextualized = await contextualize_chunks(result.children, result.parents)
    await append_job_log(job_id, "Contextualization complete.")

    await append_job_log(job_id, "Storing parents...")
    await store_parents(result.parents)
    await append_job_log(job_id, "Embedding and storing children...")
    await embed_and_store_children(contextualized)

    return {
        "parents": len(result.parents),
        "children": len(contextualized),
        "title": source_title,
        "document_type": doc_type,
    }


async def _run_url_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    url = payload["url"]
    use_cache = bool(payload.get("use_cache", True))

    await append_job_log(job["job_id"], f"Crawling {url}...")
    doc = await crawl_url(url, use_cache=use_cache)
    await append_job_log(job["job_id"], f"Fetched: {doc.title[:80]}")

    return await _process_document(
        job_id=job["job_id"],
        content=doc.content,
        source_url=doc.url,
        source_title=doc.title,
        section=doc.section,
    )


async def _run_file_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    filename = payload["filename"]
    text = payload.get("text", "")

    if not text:
        raise RuntimeError("No text payload found for file ingestion job")
    await append_job_log(job["job_id"], f"Processing uploaded file: {filename}")
    await append_job_log(job["job_id"], f"Extracted {len(text)} chars")

    return await _process_document(
        job_id=job["job_id"],
        content=text,
        source_url=f"file://{filename}",
        source_title=filename,
        section="uploaded",
    )


async def _run_corpus_job(job: dict[str, Any]) -> dict[str, Any]:
    from scripts.ingest import IRS_INSTRUCTIONS, IRS_PUBLICATIONS, IRS_TAX_TOPICS

    total_parents = 0
    total_children = 0
    pages = 0

    for base_url, paths in IRS_PUBLICATIONS.items():
        await append_job_log(job["job_id"], f"Crawling {len(paths)} IRS publications...")
        docs = await crawl_docs(base_url, paths, use_cache=True)
        for doc in docs:
            stats = await _process_document(
                job_id=job["job_id"],
                content=doc.content,
                source_url=doc.url,
                source_title=doc.title,
                section=doc.section,
            )
            total_parents += stats["parents"]
            total_children += stats["children"]
            pages += 1

    await append_job_log(job["job_id"], f"Crawling {len(IRS_TAX_TOPICS)} tax topics...")
    docs = await crawl_urls(IRS_TAX_TOPICS, use_cache=True)
    for doc in docs:
        stats = await _process_document(
            job_id=job["job_id"],
            content=doc.content,
            source_url=doc.url,
            source_title=doc.title,
            section=doc.section,
        )
        total_parents += stats["parents"]
        total_children += stats["children"]
        pages += 1

    for base_url, paths in IRS_INSTRUCTIONS.items():
        await append_job_log(job["job_id"], f"Crawling {len(paths)} form instructions...")
        docs = await crawl_docs(base_url, paths, use_cache=True)
        for doc in docs:
            stats = await _process_document(
                job_id=job["job_id"],
                content=doc.content,
                source_url=doc.url,
                source_title=doc.title,
                section=doc.section,
            )
            total_parents += stats["parents"]
            total_children += stats["children"]
            pages += 1

    return {
        "parents": total_parents,
        "children": total_children,
        "title": f"IRS Corpus ({pages} docs)",
        "document_type": "mixed",
    }


async def process_job(job: dict[str, Any], worker_id: str) -> None:
    job_id = job["job_id"]
    await append_job_log(job_id, f"Worker {worker_id} picked up job.")
    try:
        if job["job_type"] == "url":
            stats = await _run_url_job(job)
        elif job["job_type"] == "file":
            stats = await _run_file_job(job)
        elif job["job_type"] == "corpus":
            stats = await _run_corpus_job(job)
        else:
            raise RuntimeError(f"Unsupported ingestion job type: {job['job_type']}")

        await update_job_progress(job_id, {"stats": stats})
        await append_job_log(job_id, "Done.")
        await complete_job(job_id, stats)
    except Exception as exc:
        logger.exception("Ingestion job failed: %s", job_id)
        await append_job_log(job_id, f"Error: {exc}")
        await fail_job(job_id, str(exc))


async def process_job_by_id(job_id: str, worker_id: str) -> bool:
    job = await claim_job_by_id(job_id, worker_id=worker_id)
    if not job:
        return False
    await process_job(job, worker_id=worker_id)
    return True


async def run_worker_loop(worker_id: str | None = None) -> None:
    worker_id = worker_id or f"worker-{socket.gethostname()}"
    logger.info("Starting ingestion worker loop as %s", worker_id)

    while True:
        job = None
        redis_job_id = await pop_job_id(timeout_seconds=5)
        if redis_job_id:
            job = await claim_job_by_id(redis_job_id, worker_id=worker_id)
        if not job:
            job = await claim_next_job(worker_id=worker_id)

        if not job:
            await asyncio.sleep(max(settings.ingestion_worker_poll_ms, 200) / 1000)
            continue

        await process_job(job, worker_id=worker_id)


async def resume_queued_jobs_inline() -> None:
    """Process all currently queued jobs inline (useful fallback for single-service deploys)."""
    while True:
        job = await claim_next_job(worker_id="web-inline")
        if not job:
            break
        await process_job(job, worker_id="web-inline")


async def get_task_view(job_id: str) -> dict[str, Any] | None:
    job = await get_job(job_id)
    if not job:
        return None
    progress = job.get("progress", {})
    return {
        "task_id": job["job_id"],
        "status": "running" if job["status"] == "queued" else job["status"],
        "url": job["source"],
        "stats": progress.get("stats"),
        "error": job["error"],
        "logs": job.get("logs", []),
    }
