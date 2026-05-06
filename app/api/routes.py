"""FastAPI application — QA endpoint, agent, observability, eval, pipeline trace."""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool, close_pool
from app.retrieval.context_builder import retrieve
from app.generation.answerer import generate_answer
from app.agent.routes import router as agent_router
from app.api.eval_routes import router as eval_router
from app.api.doc_routes import router as doc_router
from app.ws.routes import router as ws_router
from app.telemetry import (
    setup_telemetry, get_tracer, get_current_trace_id,
    get_collector, get_metrics_snapshot,
    record_request_latency,
)
from app.middleware.query_logger import log_query, QueryLogEntry


@asynccontextmanager
async def lifespan(app: FastAPI):
    collector = setup_telemetry()
    app.state.trace_collector = collector
    app.state.startup_db_error = ""
    try:
        await get_pool()
    except Exception as exc:
        # Keep API booting so platform healthchecks can still reach /health.
        app.state.startup_db_error = str(exc)
    yield
    await close_pool()


app = FastAPI(title="Ovidius Doc QA", version="0.1.0", lifespan=lifespan)
app.include_router(agent_router)
app.include_router(eval_router)
app.include_router(doc_router)
app.include_router(ws_router)

STATIC_DIR = Path(__file__).parent.parent.parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


def _is_demo_authorized(request: Request) -> bool:
    access_cookie = request.cookies.get(settings.demo_access_cookie_name, "")
    return access_cookie == settings.demo_access_code


def _render_demo_landing(error: str = "") -> HTMLResponse:
    error_html = (
        f'<p style="color:#fecaca;font-size:13px;margin:0 0 12px 0;padding:10px 12px;border:1px solid rgba(248,113,113,0.45);background:rgba(127,29,29,0.35);border-radius:10px;">{error}</p>'
        if error
        else ""
    )
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Ovidius Demo Access</title>
    <style>
      body {{
        margin: 0;
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: radial-gradient(1200px 700px at 12% 10%, #1e3a8a 0%, rgba(30, 58, 138, 0) 55%),
                    radial-gradient(1000px 600px at 90% 90%, #4f46e5 0%, rgba(79, 70, 229, 0) 50%),
                    linear-gradient(145deg, #050816 0%, #0b1224 45%, #121a2f 100%);
        color: #e2e8f0;
      }}
      .wrap {{
        min-height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 28px;
      }}
      .card {{
        width: 100%;
        max-width: 740px;
        border: 1px solid rgba(148, 163, 184, 0.25);
        border-radius: 18px;
        overflow: hidden;
        background: linear-gradient(180deg, rgba(15, 23, 42, 0.88), rgba(15, 23, 42, 0.75));
        box-shadow: 0 30px 80px rgba(2, 6, 23, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(6px);
      }}
      .hero {{
        padding: 30px 30px 20px 30px;
        border-bottom: 1px solid rgba(148, 163, 184, 0.2);
        background:
          radial-gradient(700px 180px at -10% 0%, rgba(59, 130, 246, 0.35), rgba(59, 130, 246, 0)),
          radial-gradient(600px 160px at 105% 10%, rgba(99, 102, 241, 0.4), rgba(99, 102, 241, 0));
      }}
      .badge {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 6px 11px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: #bfdbfe;
        border: 1px solid rgba(96, 165, 250, 0.4);
        background: rgba(37, 99, 235, 0.18);
      }}
      h1 {{
        margin: 14px 0 8px 0;
        font-size: 33px;
        line-height: 1.15;
        color: #f8fafc;
        letter-spacing: -0.02em;
      }}
      p {{
        margin: 0 0 14px 0;
        color: #cbd5e1;
      }}
      ul {{
        margin: 14px 0 0 0;
        padding: 0;
        list-style: none;
        display: grid;
        gap: 8px;
      }}
      li {{
        display: flex;
        align-items: flex-start;
        gap: 10px;
        color: #dbeafe;
        font-size: 14px;
      }}
      li::before {{
        content: "✦";
        color: #93c5fd;
        margin-top: 1px;
      }}
      .form {{
        padding: 22px 30px 28px 30px;
      }}
      .label {{
        display: block;
        margin-bottom: 8px;
        font-size: 12px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #94a3b8;
        font-weight: 600;
      }}
      input {{
        width: 100%;
        box-sizing: border-box;
        border: 1px solid rgba(148, 163, 184, 0.45);
        border-radius: 12px;
        padding: 13px 14px;
        font-size: 14px;
        margin-bottom: 14px;
        color: #e2e8f0;
        background: rgba(15, 23, 42, 0.55);
        outline: none;
        transition: border-color 120ms ease, box-shadow 120ms ease;
      }}
      input::placeholder {{
        color: #64748b;
      }}
      input:focus {{
        border-color: #60a5fa;
        box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.2);
      }}
      button {{
        border: none;
        border-radius: 12px;
        background: linear-gradient(180deg, #3b82f6, #2563eb);
        color: #eff6ff;
        font-size: 14px;
        font-weight: 600;
        padding: 11px 18px;
        cursor: pointer;
        box-shadow: 0 8px 20px rgba(37, 99, 235, 0.35);
        transition: transform 120ms ease, box-shadow 120ms ease, opacity 120ms ease;
      }}
      button:hover {{
        transform: translateY(-1px);
        box-shadow: 0 12px 24px rgba(37, 99, 235, 0.45);
      }}
      button:active {{
        transform: translateY(0);
      }}
      .inline {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }}
      .muted {{
        margin-top: 14px;
        font-size: 12px;
        color: #94a3b8;
      }}
      .hint {{
        color: #60a5fa;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 12px;
        border: 1px solid rgba(96, 165, 250, 0.35);
        background: rgba(30, 64, 175, 0.18);
        padding: 6px 10px;
        border-radius: 999px;
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="card">
        <div class="hero">
          <span class="badge">Private Preview</span>
          <h1>Ovidius AI Demo Portal</h1>
          <p>Explore tax-focused RAG workflows with transparent reasoning, citation-backed answers, and deep observability tooling.</p>
          <ul>
            <li>Ask complex tax questions and receive structured, source-cited responses</li>
            <li>Inspect retrieval, reranking, and generation pipeline health in real time</li>
            <li>Ingest domain content and validate quality with trace-level visibility</li>
          </ul>
        </div>
        <div class="form">
          {error_html}
          <form method="post" action="/demo-access">
            <label class="label" for="access_code">Access Code</label>
            <input id="access_code" type="password" name="access_code" placeholder="Enter your team access code" required />
            <div class="inline">
              <button type="submit">Enter Demo</button>
              <span class="hint">Secure access enabled</span>
            </div>
          </form>
          <p class="muted">Access is restricted. Contact the Ovidius team if you need a demo key.</p>
        </div>
      </div>
    </div>
  </body>
</html>"""
    return HTMLResponse(content=html)


@app.middleware("http")
async def gate_dashboard_assets(request: Request, call_next):
    if request.url.path.startswith("/assets") and not _is_demo_authorized(request):
        return RedirectResponse(url="/demo-access", status_code=307)
    return await call_next(request)


@app.get("/demo-access", response_class=HTMLResponse)
async def demo_access_page():
    return _render_demo_landing()


@app.post("/demo-access")
async def demo_access_submit(request: Request, access_code: str = Form(...)):
    if access_code != settings.demo_access_code:
        return _render_demo_landing(error="Invalid access code. Please try again.")
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=settings.demo_access_cookie_name,
        value=settings.demo_access_code,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@app.post("/demo-logout")
async def demo_logout():
    response = RedirectResponse(url="/demo-access", status_code=303)
    response.delete_cookie(settings.demo_access_cookie_name)
    return response


# ---------------------------------------------------------------------------
# QA endpoint
# ---------------------------------------------------------------------------

class QARequest(BaseModel):
    question: str
    top_k: int = 5


class CitationResponse(BaseModel):
    index: int
    source_url: str
    source_title: str


class PipelineStep(BaseModel):
    step: str
    duration_ms: float
    detail: str = ""


class QAResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    confidence: str
    retrieval_method: str
    chunks_used: int
    parent_chunks_used: int
    pipeline: list[PipelineStep]
    total_ms: float
    trace_id: str | None = None


@app.post("/qa", response_model=QAResponse)
async def ask_question(req: QARequest):
    tracer = get_tracer("api")
    pipeline_steps = []
    total_start = time.perf_counter()

    with tracer.start_as_current_span("qa_request") as span:
        trace_id = get_current_trace_id()
        span.set_attribute("question", req.question[:500])
        span.set_attribute("top_k", req.top_k)
        span.set_attribute("interface", "api")

        t0 = time.perf_counter()
        retrieval_result = await retrieve(req.question, top_k=req.top_k)
        retrieve_ms = round((time.perf_counter() - t0) * 1000, 1)
        pipeline_steps.append(PipelineStep(
            step="hybrid_search_rerank_correct",
            duration_ms=retrieve_ms,
            detail=(
                f"confidence={retrieval_result.corrective.confidence.value}, "
                f"filtered={retrieval_result.corrective.filtered_count}/{retrieval_result.corrective.original_count}"
                f"{', query_retried' if retrieval_result.retry_performed else ''}"
            ),
        ))

        t0 = time.perf_counter()
        result = await generate_answer(req.question, retrieval_result)
        generate_ms = round((time.perf_counter() - t0) * 1000, 1)
        pipeline_steps.append(PipelineStep(
            step="generate_answer",
            duration_ms=generate_ms,
            detail=f"model={settings.generation_model}, parents={result.parent_chunks_used}",
        ))

        total_ms = round((time.perf_counter() - total_start) * 1000, 1)
        span.set_attribute("total_ms", total_ms)
        span.set_attribute("confidence", result.confidence)
        record_request_latency(total_ms, interface="api")

        asyncio.create_task(log_query(QueryLogEntry(
            question=req.question,
            answer=result.answer[:1000],
            citations=[{"index": c.index, "source_url": c.source_url, "source_title": c.source_title} for c in result.citations],
            confidence=result.confidence,
            retrieval_method=result.retrieval_method,
            pipeline_steps=[{"step": s.step, "duration_ms": s.duration_ms, "detail": s.detail} for s in pipeline_steps],
            chunks_used=result.chunks_used,
            parent_chunks_used=result.parent_chunks_used,
            latency_ms=total_ms,
            retrieval_ms=retrieve_ms,
            generation_ms=generate_ms,
            trace_id=trace_id,
            interface="api",
        )))

    return QAResponse(
        answer=result.answer,
        citations=[
            CitationResponse(
                index=c.index,
                source_url=c.source_url,
                source_title=c.source_title,
            )
            for c in result.citations
        ],
        confidence=result.confidence,
        retrieval_method=result.retrieval_method,
        chunks_used=result.chunks_used,
        parent_chunks_used=result.parent_chunks_used,
        pipeline=pipeline_steps,
        total_ms=total_ms,
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            child_count = await conn.fetchval("SELECT count(*) FROM documents")
            parent_count = await conn.fetchval("SELECT count(*) FROM parent_chunks")
        startup_db_error = getattr(app.state, "startup_db_error", "")
        return {
            "status": "ok" if not startup_db_error else "degraded",
            "child_chunks": child_count,
            "parent_chunks": parent_count,
            "startup_db_error": startup_db_error,
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "child_chunks": 0,
            "parent_chunks": 0,
            "startup_db_error": str(exc),
        }


# ---------------------------------------------------------------------------
# Observability endpoints
# ---------------------------------------------------------------------------

@app.get("/traces")
async def list_traces(limit: int = 50):
    collector = get_collector()
    if not collector:
        return []
    return collector.get_recent_traces(limit=limit)


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    collector = get_collector()
    if not collector:
        raise HTTPException(status_code=404, detail="Trace collector not initialized")
    trace = collector.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@app.get("/metrics")
async def metrics_endpoint():
    return get_metrics_snapshot()


@app.get("/query-logs")
async def get_query_logs(
    interface: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = []
        params = []
        idx = 1

        if interface:
            conditions.append(f"interface = ${idx}")
            params.append(interface)
            idx += 1
        if session_id:
            conditions.append(f"session_id = ${idx}")
            params.append(session_id)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])

        rows = await conn.fetch(
            f"""SELECT id, question, answer, citations, confidence, retrieval_method,
                       pipeline_steps, chunks_used, parent_chunks_used,
                       latency_ms, retrieval_ms, generation_ms,
                       trace_id, session_id, interface, created_at
                FROM query_logs {where}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}""",
            *params,
        )

    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/")
async def dashboard(request: Request):
    if not _is_demo_authorized(request):
        return RedirectResponse(url="/demo-access", status_code=307)
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return {"status": "ok", "message": "Ovidius Doc QA API. Dashboard not yet built."}
    return FileResponse(str(index))
