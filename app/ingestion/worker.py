"""Ingestion worker runtime for processing queued ingestion jobs."""

from __future__ import annotations

import asyncio
import io
import logging
import socket
import time
from typing import Any

from app.config import settings
from app.ingestion.chunker import chunk_document
from app.ingestion.document_classifier import classify_document_metadata
from app.ingestion.contextualizer import contextualize_chunks
from app.ingestion.crawler import crawl_url
from app.ingestion.embedder import embed_and_store_children, store_parents
from app.ingestion.job_queue import (
    append_job_log,
    claim_job_by_id,
    claim_next_job,
    complete_job,
    fail_job,
    get_job,
    mark_job_paused,
    pop_job_id,
    recover_stale_running_jobs,
    update_job_progress,
)

logger = logging.getLogger(__name__)

PIPELINE_STEPS = ["classify_metadata", "chunking", "contextualizing", "storing_parents", "embedding_children"]


class PauseRequested(Exception):
    """Raised when a running job should be paused at next safe checkpoint."""


def _extract_text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _build_pipeline_steps(
    active: str | None = None,
    completed: set[str] | None = None,
    skipped: set[str] | None = None,
) -> dict[str, str]:
    completed = completed or set()
    skipped = skipped or set()
    states: dict[str, str] = {}
    for step in PIPELINE_STEPS:
        if step in skipped:
            states[step] = "skipped"
        elif step == active:
            states[step] = "running"
        elif step in completed:
            states[step] = "complete"
        else:
            states[step] = "pending"
    return states


async def _process_document(
    job_id: str,
    content: str,
    source_url: str,
    source_title: str,
    section: str = "",
    current_doc: int | None = None,
    total_docs: int | None = None,
) -> dict[str, Any]:
    shared_progress: dict[str, Any] = {
        "phase": "processing",
        "current_title": source_title,
        "current_url": source_url,
    }
    if current_doc is not None:
        shared_progress["current_doc"] = current_doc
    if total_docs is not None:
        shared_progress["total_docs"] = total_docs

    await update_job_progress(
        job_id,
        {
            **shared_progress,
            "pipeline_stage": "classify_metadata",
            "pipeline_steps": _build_pipeline_steps(active="classify_metadata"),
        },
    )
    t0 = time.perf_counter()
    metadata = await classify_document_metadata(
        content=content,
        source_url=source_url,
        source_title=source_title,
        default_section=section,
    )
    classify_ms = round((time.perf_counter() - t0) * 1000, 1)
    effective_section = metadata.section or section
    await append_job_log(
        job_id,
        "Metadata classified: "
        f"type={metadata.doc_type}, section={effective_section}, "
        f"topics={metadata.tax_topics or []}, tags={metadata.metadata_tags or []} "
        f"({classify_ms}ms, llm={'yes' if metadata.llm_used else 'no'})",
    )
    if metadata.reason:
        await append_job_log(job_id, f"Classification note: {metadata.reason[:220]}")

    await update_job_progress(
        job_id,
        {
            **shared_progress,
            "metadata_labels": {
                "doc_type": metadata.doc_type,
                "section": effective_section,
                "tax_topics": metadata.tax_topics,
                "metadata_tags": metadata.metadata_tags,
                "llm_used": metadata.llm_used,
            },
            "pipeline_stage": "chunking",
            "pipeline_steps": _build_pipeline_steps(
                active="chunking",
                completed={"classify_metadata"},
            ),
        },
    )

    t0 = time.perf_counter()
    result = chunk_document(
        content,
        source_url,
        source_title,
        effective_section,
        doc_type_override=metadata.doc_type,
    )
    chunk_ms = round((time.perf_counter() - t0) * 1000, 1)
    doc_type = result.parents[0].document_type if result.parents else "unknown"
    await append_job_log(
        job_id,
        f"Chunked: {len(result.parents)} parents, {len(result.children)} children ({doc_type}) in {chunk_ms}ms",
    )

    if not result.children:
        await append_job_log(job_id, "No chunks produced - skipping.")
        await update_job_progress(
            job_id,
            {
                **shared_progress,
                "metadata_labels": {
                    "doc_type": metadata.doc_type,
                    "section": effective_section,
                    "tax_topics": metadata.tax_topics,
                    "metadata_tags": metadata.metadata_tags,
                    "llm_used": metadata.llm_used,
                },
                "pipeline_stage": "done",
                "pipeline_steps": _build_pipeline_steps(
                    completed={"classify_metadata", "chunking"},
                    skipped={"contextualizing", "storing_parents", "embedding_children"},
                ),
            },
        )
        return {"parents": 0, "children": 0, "title": source_title, "document_type": doc_type}

    await update_job_progress(
        job_id,
        {
            **shared_progress,
            "pipeline_stage": "contextualizing",
            "pipeline_steps": _build_pipeline_steps(
                active="contextualizing",
                completed={"classify_metadata", "chunking"},
            ),
        },
    )
    total_chunks = len(result.children)
    await append_job_log(job_id, f"Contextualizing {total_chunks} chunks...")
    t0 = time.perf_counter()
    last_logged = 0

    async def _ctx_progress(done: int, total: int, parent_label: str) -> None:
        nonlocal last_logged
        pct = round(done / total * 100) if total else 100
        should_log = (
            done == 1
            or done == total
            or done - last_logged >= max(10, total // 10)
        )
        if should_log:
            last_logged = done
            elapsed = round((time.perf_counter() - t0) * 1000)
            await append_job_log(
                job_id,
                f"  Contextualizing {done}/{total} ({pct}%) — \"{parent_label}\" ({elapsed}ms elapsed)",
            )
            await update_job_progress(
                job_id,
                {
                    **shared_progress,
                    "pipeline_stage": "contextualizing",
                    "pipeline_steps": _build_pipeline_steps(
                        active="contextualizing",
                        completed={"classify_metadata", "chunking"},
                    ),
                    "contextualize_done": done,
                    "contextualize_total": total,
                    "contextualize_pct": pct,
                },
            )

    contextualized = await contextualize_chunks(result.children, result.parents, on_progress=_ctx_progress)
    contextualize_ms = round((time.perf_counter() - t0) * 1000, 1)
    await append_job_log(job_id, f"Contextualization complete — {total_chunks} chunks in {contextualize_ms}ms.")

    await update_job_progress(
        job_id,
        {
            **shared_progress,
            "pipeline_stage": "storing_parents",
            "pipeline_steps": _build_pipeline_steps(
                active="storing_parents", completed={"classify_metadata", "chunking", "contextualizing"}
            ),
        },
    )
    await append_job_log(job_id, "Storing parents...")
    t0 = time.perf_counter()
    await store_parents(result.parents)
    store_ms = round((time.perf_counter() - t0) * 1000, 1)
    await append_job_log(job_id, f"Stored parents in {store_ms}ms.")

    await update_job_progress(
        job_id,
        {
            **shared_progress,
            "pipeline_stage": "embedding_children",
            "pipeline_steps": _build_pipeline_steps(
                active="embedding_children",
                completed={"classify_metadata", "chunking", "contextualizing", "storing_parents"},
            ),
        },
    )
    await append_job_log(job_id, "Embedding and storing children...")
    t0 = time.perf_counter()
    await embed_and_store_children(contextualized)
    embed_ms = round((time.perf_counter() - t0) * 1000, 1)
    await append_job_log(job_id, f"Embedded and stored children in {embed_ms}ms.")

    await update_job_progress(
        job_id,
        {
            **shared_progress,
            "pipeline_stage": "done",
            "pipeline_steps": _build_pipeline_steps(completed=set(PIPELINE_STEPS)),
        },
    )

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

    await update_job_progress(job["job_id"], {"phase": "crawling", "completion": 10, "current_url": url})
    await append_job_log(job["job_id"], f"Crawling {url}...")
    doc = await crawl_url(url, use_cache=use_cache)
    await append_job_log(job["job_id"], f"Fetched: {doc.title[:80]}")
    await update_job_progress(
        job["job_id"],
        {"phase": "processing", "completion": 45, "current_title": doc.title, "current_url": doc.url},
    )

    stats = await _process_document(
        job_id=job["job_id"],
        content=doc.content,
        source_url=doc.url,
        source_title=doc.title,
        section=doc.section,
        current_doc=1,
        total_docs=1,
    )
    await update_job_progress(
        job["job_id"],
        {
            "phase": "processing",
            "completion": 95,
            "processed_docs": 1,
            "total_docs": 1,
            "crawled_docs": 1,
        },
    )
    return stats


async def _run_file_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = job["payload"]
    filename = payload["filename"]
    text = payload.get("text", "")

    if not text:
        raise RuntimeError("No text payload found for file ingestion job")
    await append_job_log(job["job_id"], f"Processing uploaded file: {filename}")
    await append_job_log(job["job_id"], f"Extracted {len(text)} chars")
    await update_job_progress(
        job["job_id"],
        {
            "phase": "processing",
            "completion": 35,
            "current_title": filename,
            "current_url": f"file://{filename}",
            "total_docs": 1,
        },
    )

    stats = await _process_document(
        job_id=job["job_id"],
        content=text,
        source_url=f"file://{filename}",
        source_title=filename,
        section="uploaded",
        current_doc=1,
        total_docs=1,
    )
    await update_job_progress(
        job["job_id"],
        {"phase": "processing", "completion": 95, "processed_docs": 1, "crawled_docs": 1, "total_docs": 1},
    )
    return stats


async def _run_corpus_job(job: dict[str, Any]) -> dict[str, Any]:
    from scripts.ingest import IRS_INSTRUCTIONS, IRS_PUBLICATIONS, IRS_TAX_TOPICS

    progress = job.get("progress") or {}
    corpus_progress = progress.get("corpus_progress") or {}
    resume_index = int(corpus_progress.get("next_index", 0))
    total_parents = int(corpus_progress.get("parents", 0))
    total_children = int(corpus_progress.get("children", 0))
    pages = int(corpus_progress.get("processed_docs", 0))

    publication_urls: list[str] = []
    for base_url, paths in IRS_PUBLICATIONS.items():
        for path in dict.fromkeys(paths):
            publication_urls.append(f"{base_url.rstrip('/')}/{path.lstrip('/')}")
    topic_urls = list(dict.fromkeys(IRS_TAX_TOPICS))
    instruction_urls: list[str] = []
    for base_url, paths in IRS_INSTRUCTIONS.items():
        for path in dict.fromkeys(paths):
            instruction_urls.append(f"{base_url.rstrip('/')}/{path.lstrip('/')}")

    all_urls = publication_urls + topic_urls + instruction_urls
    total_docs = len(all_urls)
    crawled_docs = 0
    failed_crawls = 0

    await append_job_log(
        job["job_id"],
        f"Corpus plan: {len(publication_urls)} publications, {len(topic_urls)} tax topics, {len(instruction_urls)} instructions ({total_docs} total).",
    )

    await update_job_progress(
        job["job_id"],
        {
            "phase": "crawling",
            "completion": 0,
            "total_docs": total_docs,
            "crawled_docs": 0,
            "processed_docs": pages,
            "failed_crawls": failed_crawls,
        },
    )

    all_docs = []
    for idx, url in enumerate(all_urls):
        await append_job_log(job["job_id"], f"Crawling [{idx + 1}/{total_docs}] {url}")
        try:
            doc = await crawl_url(url, use_cache=True)
            all_docs.append(doc)
            crawled_docs += 1
            await append_job_log(job["job_id"], f"Crawled [{crawled_docs}/{total_docs}] {doc.title[:90]}")
        except Exception as exc:
            failed_crawls += 1
            crawled_docs += 1
            await append_job_log(job["job_id"], f"Crawl failed [{crawled_docs}/{total_docs}] {url} ({exc})")
        await update_job_progress(
            job["job_id"],
            {
                "phase": "crawling",
                "total_docs": total_docs,
                "crawled_docs": crawled_docs,
                "processed_docs": pages,
                "failed_crawls": failed_crawls,
                "completion": round((crawled_docs / max(total_docs, 1)) * 50, 1),
                "current_url": url,
            },
        )

    if resume_index > 0:
        await append_job_log(
            job["job_id"],
            f"Resuming corpus ingest at doc {min(resume_index + 1, total_docs)}/{total_docs}.",
        )

    await append_job_log(job["job_id"], f"Processing {len(all_docs)} crawled docs...")

    for idx, doc in enumerate(all_docs):
        fresh = await get_job(job["job_id"])
        fresh_progress = (fresh or {}).get("progress", {})
        if fresh_progress.get("pause_requested"):
            await append_job_log(job["job_id"], "Pause requested. Checkpoint saved; job paused.")
            raise PauseRequested()

        if idx < resume_index:
            continue
        await append_job_log(job["job_id"], f"Processing [{idx + 1}/{len(all_docs)}] {doc.title[:90]}")
        stats = await _process_document(
            job_id=job["job_id"],
            content=doc.content,
            source_url=doc.url,
            source_title=doc.title,
            section=doc.section,
            current_doc=idx + 1,
            total_docs=len(all_docs),
        )
        total_parents += stats["parents"]
        total_children += stats["children"]
        pages += 1
        await update_job_progress(
            job["job_id"],
            {
                "phase": "processing",
                "completion": round(50 + ((pages / max(total_docs, 1)) * 50), 1),
                "current_title": doc.title,
                "current_url": doc.url,
                "corpus_progress": {
                    "next_index": idx + 1,
                    "processed_docs": pages,
                    "total_docs": total_docs,
                    "parents": total_parents,
                    "children": total_children,
                    "crawled_docs": crawled_docs,
                    "failed_crawls": failed_crawls,
                }
            },
        )
        await append_job_log(
            job["job_id"],
            f"Processed [{pages}/{total_docs}] parents={total_parents}, children={total_children}",
        )

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
        await update_job_progress(job_id, {"phase": "starting", "completion": 2})
        if job["job_type"] == "url":
            stats = await _run_url_job(job)
        elif job["job_type"] == "file":
            stats = await _run_file_job(job)
        elif job["job_type"] == "corpus":
            stats = await _run_corpus_job(job)
        else:
            raise RuntimeError(f"Unsupported ingestion job type: {job['job_type']}")

        await update_job_progress(job_id, {"stats": stats})
        await update_job_progress(job_id, {"phase": "completed", "completion": 100})
        await append_job_log(job_id, "Done.")
        await complete_job(job_id, stats)

        from app.cache import invalidate_responses
        await invalidate_responses()
    except PauseRequested:
        await update_job_progress(job_id, {"phase": "paused"})
        await mark_job_paused(job_id)
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
    recovered = await recover_stale_running_jobs(settings.ingestion_stale_after_seconds)
    if recovered:
        logger.info("Recovered %s stale ingestion jobs", len(recovered))

    while True:
        job = None
        redis_job_id = await pop_job_id()
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
        "status": job["status"],
        "url": job["source"],
        "stats": progress.get("stats"),
        "progress": progress,
        "error": job["error"],
        "logs": job.get("logs", []),
    }
