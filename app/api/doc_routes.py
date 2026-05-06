"""Document browsing + durable ingestion queue endpoints."""

import asyncio
import io
from typing import Literal

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool
from app.ingestion.job_queue import enqueue_job, get_job, list_jobs, request_pause, resume_job, clear_pause_request
from app.ingestion.worker import process_job_by_id

router = APIRouter(prefix="/api")


class IngestRequest(BaseModel):
    url: str
    use_cache: bool = True
    dedup_mode: Literal["skip", "force_reingest"] = "skip"


@router.get("/documents")
async def list_documents(limit: int = 100, offset: int = 0):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT p.parent_id, p.source_url, p.source_title, p.section,
                   p.document_type, p.token_count, p.created_at,
                   COUNT(d.chunk_id) AS child_count
            FROM parent_chunks p
            LEFT JOIN documents d ON d.parent_id = p.parent_id
            GROUP BY p.parent_id, p.source_url, p.source_title, p.section,
                     p.document_type, p.token_count, p.created_at
            ORDER BY p.created_at DESC
            LIMIT $1 OFFSET $2
        """, limit, offset)

        total = await conn.fetchval("SELECT COUNT(*) FROM parent_chunks")

    return {"documents": [dict(r) for r in rows], "total": total}


@router.get("/documents/{parent_id}")
async def get_document_detail(parent_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        parent = await conn.fetchrow("""
            SELECT parent_id, content, source_url, source_title, section,
                   document_type, token_count, created_at
            FROM parent_chunks WHERE parent_id = $1
        """, parent_id)

        if not parent:
            raise HTTPException(status_code=404, detail="Document not found")

        chunks = await conn.fetch("""
            SELECT chunk_id, content, contextual_content, token_count, section
            FROM documents WHERE parent_id = $1
            ORDER BY chunk_id
        """, parent_id)

    return {"parent": dict(parent), "chunks": [dict(c) for c in chunks]}


@router.delete("/documents/{parent_id}")
async def delete_document(parent_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        child_result = await conn.execute(
            "DELETE FROM documents WHERE parent_id = $1", parent_id,
        )
        deleted_children = int(child_result.split()[-1])
        parent_result = await conn.execute(
            "DELETE FROM parent_chunks WHERE parent_id = $1", parent_id,
        )
        deleted_parent = int(parent_result.split()[-1])

    if not deleted_parent:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted_parent": deleted_parent, "deleted_children": deleted_children}


@router.post("/ingest/url")
async def ingest_url(req: IngestRequest):
    task_id = await enqueue_job(
        job_type="url",
        source=req.url,
        payload={"url": req.url, "use_cache": req.use_cache, "dedup_mode": req.dedup_mode},
    )
    if settings.ingestion_inline_worker:
        asyncio.create_task(process_job_by_id(task_id, worker_id="web-inline"))
    return {"task_id": task_id, "status": "queued"}


@router.get("/ingest/tasks")
async def list_ingest_tasks():
    jobs = await list_jobs(limit=100)
    tasks = []
    for job in jobs:
        progress = job.get("progress", {})
        tasks.append({
            "task_id": job["job_id"],
            "status": job["status"],
            "url": job["source"],
            "stats": progress.get("stats"),
            "progress": progress,
            "error": job["error"],
            "logs": job.get("logs", []),
        })
    return tasks


@router.get("/ingest/tasks/{task_id}")
async def get_ingest_task(task_id: str):
    job = await get_job(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task not found")
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


@router.post("/ingest/tasks/{task_id}/pause")
async def pause_ingest_task(task_id: str):
    job = await request_pause(task_id)
    if not job:
        raise HTTPException(status_code=400, detail="Task cannot be paused in its current state")
    await asyncio.sleep(0)
    fresh = await get_job(task_id)
    progress = (fresh or {}).get("progress", {})
    return {
        "task_id": (fresh or job)["job_id"],
        "status": (fresh or job)["status"],
        "url": (fresh or job)["source"],
        "stats": progress.get("stats"),
        "progress": progress,
        "error": (fresh or job)["error"],
        "logs": (fresh or job).get("logs", []),
    }


@router.post("/ingest/tasks/{task_id}/resume")
async def resume_ingest_task(task_id: str):
    job = await resume_job(task_id)
    if not job:
        # If still running but pause was requested, "resume" means clear that request.
        job = await clear_pause_request(task_id)
        if job:
            await asyncio.sleep(0)
            fresh = await get_job(task_id)
            progress = (fresh or {}).get("progress", {})
            return {
                "task_id": (fresh or job)["job_id"],
                "status": (fresh or job)["status"],
                "url": (fresh or job)["source"],
                "stats": progress.get("stats"),
                "progress": progress,
                "error": (fresh or job)["error"],
                "logs": (fresh or job).get("logs", []),
            }
    if not job:
        raise HTTPException(status_code=400, detail="Task is neither paused nor awaiting pause")
    await asyncio.sleep(0)
    if settings.ingestion_inline_worker:
        asyncio.create_task(process_job_by_id(task_id, worker_id="web-inline"))
    fresh = await get_job(task_id)
    progress = (fresh or {}).get("progress", {})
    return {
        "task_id": (fresh or job)["job_id"],
        "status": (fresh or job)["status"],
        "url": (fresh or job)["source"],
        "stats": progress.get("stats"),
        "progress": progress,
        "error": (fresh or job)["error"],
        "logs": (fresh or job).get("logs", []),
    }


def _extract_text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


@router.post("/ingest/corpus")
async def ingest_corpus(dedup_mode: Literal["skip", "force_reingest"] = "skip"):
    task_id = await enqueue_job(
        job_type="corpus",
        source="corpus://irs-full",
        payload={"dedup_mode": dedup_mode},
    )
    if settings.ingestion_inline_worker:
        asyncio.create_task(process_job_by_id(task_id, worker_id="web-inline"))
    return {"task_id": task_id, "status": "queued"}


@router.post("/ingest/file")
async def ingest_file(
    file: UploadFile = File(...),
    dedup_mode: Literal["skip", "force_reingest"] = Form("skip"),
):
    data = await file.read()
    filename = file.filename or "uploaded_document"

    if filename.lower().endswith(".pdf"):
        try:
            text = _extract_text_from_pdf(data)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="File must be a PDF or UTF-8 text file")

    if not text.strip():
        raise HTTPException(status_code=400, detail="No text content extracted from file")

    task_id = await enqueue_job(
        job_type="file",
        source=f"file://{filename}",
        payload={"filename": filename, "text": text, "dedup_mode": dedup_mode},
    )
    if settings.ingestion_inline_worker:
        asyncio.create_task(process_job_by_id(task_id, worker_id="web-inline"))
    return {"task_id": task_id, "status": "queued", "filename": filename}
