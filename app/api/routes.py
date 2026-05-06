"""FastAPI application — QA endpoint, agent, observability, eval, pipeline trace."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from app.config import settings
from app.db import get_pool, close_pool
from app.ingestion.job_queue import recover_stale_running_jobs
from app.ingestion.worker import resume_queued_jobs_inline
from app.retrieval.context_builder import retrieve
from app.generation.answerer import generate_answer
from app.cache import get_cached_response, cache_response, get_cache_stats, close_client as close_redis
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

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    collector = setup_telemetry()
    app.state.trace_collector = collector
    app.state.startup_db_error = ""
    try:
        await get_pool()
        if settings.ingestion_inline_worker:
            recovered = await recover_stale_running_jobs(settings.ingestion_stale_after_seconds)
            if recovered:
                logger.info("Recovered %s stale ingestion jobs: %s", len(recovered), ", ".join(recovered))
            app.state.inline_ingestion_task = asyncio.create_task(resume_queued_jobs_inline())
    except Exception as exc:
        # Keep API booting so platform healthchecks can still reach /health.
        app.state.startup_db_error = str(exc)
    yield
    inline_task = getattr(app.state, "inline_ingestion_task", None)
    if inline_task and not inline_task.done():
        inline_task.cancel()
    await close_redis()
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
        f'<div class="error">{error}</div>'
        if error
        else ""
    )
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Ovidius &mdash; Documentation QA Agent</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
    <style>
      *,*::before,*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
      body {{
        font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #fafbfc;
        color: #1a1a2e;
        -webkit-font-smoothing: antialiased;
      }}

      /* --- NAV --- */
      .nav {{
        position: fixed; top: 0; left: 0; right: 0; z-index: 50;
        display: flex; align-items: center; justify-content: space-between;
        padding: 16px 32px;
        background: rgba(255,255,255,0.85);
        backdrop-filter: blur(12px);
        border-bottom: 1px solid #e8ecf1;
      }}
      .nav-brand {{
        display: flex; align-items: center; gap: 10px;
        font-size: 17px; font-weight: 700; color: #1a1a2e;
        text-decoration: none;
      }}
      .nav-brand svg {{ color: #4361ee; }}
      .nav-tag {{
        font-size: 11px; font-weight: 600; letter-spacing: 0.04em;
        padding: 3px 10px; border-radius: 999px;
        background: #eef2ff; color: #4361ee;
      }}

      /* --- HERO --- */
      .hero {{
        padding: 120px 32px 64px;
        text-align: center;
        background:
          radial-gradient(ellipse 800px 500px at 50% 0%, rgba(67,97,238,0.08), transparent),
          radial-gradient(ellipse 600px 400px at 80% 100%, rgba(114,9,183,0.05), transparent);
      }}
      .hero-badge {{
        display: inline-flex; align-items: center; gap: 6px;
        padding: 5px 14px; border-radius: 999px;
        font-size: 12px; font-weight: 600;
        color: #4361ee; background: #eef2ff;
        border: 1px solid #dbe4ff;
        margin-bottom: 20px;
      }}
      .hero-badge .dot {{
        width: 6px; height: 6px; border-radius: 50%;
        background: #4361ee;
        animation: pulse 2s ease-in-out infinite;
      }}
      @keyframes pulse {{
        0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }}
      }}
      .hero h1 {{
        font-size: clamp(32px, 5vw, 48px);
        font-weight: 700; line-height: 1.15;
        letter-spacing: -0.03em;
        color: #1a1a2e;
        max-width: 720px; margin: 0 auto 16px;
      }}
      .hero h1 span {{ color: #4361ee; }}
      .hero p {{
        font-size: 17px; line-height: 1.65;
        color: #64748b; max-width: 580px; margin: 0 auto;
      }}

      /* --- PIPELINE --- */
      .pipeline-section {{
        padding: 0 32px 56px;
        max-width: 900px; margin: 0 auto;
      }}
      .pipeline-label {{
        text-align: center;
        font-size: 11px; font-weight: 600; text-transform: uppercase;
        letter-spacing: 0.08em; color: #94a3b8; margin-bottom: 20px;
      }}
      .pipeline {{
        display: flex; align-items: center; justify-content: center;
        gap: 0; flex-wrap: wrap;
      }}
      .pipe-step {{
        display: flex; align-items: center; gap: 0;
      }}
      .pipe-node {{
        padding: 8px 16px; border-radius: 10px;
        font-size: 12px; font-weight: 600;
        white-space: nowrap;
        border: 1px solid #e2e8f0;
        background: white;
        color: #334155;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
      }}
      .pipe-arrow {{
        color: #cbd5e1; font-size: 16px;
        padding: 0 4px;
        display: flex; align-items: center;
      }}
      .pipe-arrow svg {{ width: 16px; height: 16px; }}

      /* --- OPTIONS GRID --- */
      .options {{
        max-width: 960px; margin: 0 auto;
        padding: 0 32px 56px;
        display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 16px;
      }}
      .opt-card {{
        border: 1px solid #e8ecf1;
        border-radius: 14px;
        padding: 24px;
        background: white;
        box-shadow: 0 1px 4px rgba(0,0,0,0.03);
        transition: box-shadow 180ms ease, transform 180ms ease;
      }}
      .opt-card:hover {{
        box-shadow: 0 6px 24px rgba(0,0,0,0.06);
        transform: translateY(-2px);
      }}
      .opt-letter {{
        display: inline-flex; align-items: center; justify-content: center;
        width: 28px; height: 28px; border-radius: 8px;
        font-size: 13px; font-weight: 700;
        margin-bottom: 12px;
      }}
      .opt-a {{ background: #eef2ff; color: #4361ee; }}
      .opt-b {{ background: #f0fdf4; color: #16a34a; }}
      .opt-c {{ background: #fef3c7; color: #d97706; }}
      .opt-card h3 {{
        font-size: 15px; font-weight: 600; color: #1a1a2e;
        margin-bottom: 6px;
      }}
      .opt-card p {{
        font-size: 13px; line-height: 1.55; color: #64748b;
      }}
      .opt-card .opt-detail {{
        margin-top: 10px; padding-top: 10px;
        border-top: 1px solid #f1f5f9;
        font-size: 12px; color: #94a3b8;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      }}

      /* --- TECH STRIP --- */
      .tech-strip {{
        max-width: 720px; margin: 0 auto;
        padding: 0 32px 48px;
        display: flex; flex-wrap: wrap; justify-content: center; gap: 8px;
      }}
      .tech-chip {{
        padding: 5px 12px; border-radius: 8px;
        font-size: 12px; font-weight: 500;
        background: #f8fafc; color: #475569;
        border: 1px solid #e2e8f0;
      }}

      /* --- ACCESS CARD --- */
      .access-section {{
        max-width: 440px; margin: 0 auto;
        padding: 0 32px 80px;
      }}
      .access-card {{
        border: 1px solid #e8ecf1;
        border-radius: 16px;
        padding: 32px;
        background: white;
        box-shadow: 0 4px 24px rgba(0,0,0,0.04);
        text-align: center;
      }}
      .access-card .lock-icon {{
        width: 40px; height: 40px; border-radius: 12px;
        background: #eef2ff;
        display: inline-flex; align-items: center; justify-content: center;
        margin-bottom: 16px;
      }}
      .access-card .lock-icon svg {{
        width: 20px; height: 20px; color: #4361ee;
      }}
      .access-card h2 {{
        font-size: 18px; font-weight: 600; color: #1a1a2e;
        margin-bottom: 4px;
      }}
      .access-card .subtitle {{
        font-size: 13px; color: #94a3b8; margin-bottom: 20px;
      }}
      .access-card input {{
        width: 100%;
        border: 1.5px solid #e2e8f0;
        border-radius: 10px;
        padding: 12px 14px;
        font-size: 15px;
        font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
        letter-spacing: 0.12em;
        text-align: center;
        color: #1a1a2e;
        background: #f8fafc;
        outline: none;
        transition: border-color 150ms, box-shadow 150ms;
      }}
      .access-card input::placeholder {{
        color: #cbd5e1; letter-spacing: 0.06em;
        font-family: Inter, -apple-system, sans-serif;
        font-size: 13px;
      }}
      .access-card input:focus {{
        border-color: #4361ee;
        box-shadow: 0 0 0 3px rgba(67,97,238,0.12);
        background: white;
      }}
      .access-card button {{
        width: 100%;
        margin-top: 12px;
        border: none;
        border-radius: 10px;
        padding: 12px;
        font-size: 14px; font-weight: 600;
        color: white;
        background: #4361ee;
        cursor: pointer;
        transition: background 150ms, transform 100ms;
      }}
      .access-card button:hover {{ background: #3b55d4; }}
      .access-card button:active {{ transform: scale(0.98); }}
      .access-card .footer-note {{
        margin-top: 14px;
        font-size: 11px; color: #94a3b8;
      }}
      .error {{
        margin-bottom: 14px;
        padding: 10px 14px;
        border-radius: 10px;
        font-size: 13px;
        color: #dc2626;
        background: #fef2f2;
        border: 1px solid #fecaca;
      }}

      /* --- FOOTER --- */
      .foot {{
        text-align: center;
        padding: 24px 32px;
        font-size: 12px; color: #94a3b8;
        border-top: 1px solid #e8ecf1;
      }}
      .foot a {{ color: #4361ee; text-decoration: none; }}

      @media (max-width: 640px) {{
        .nav {{ padding: 12px 20px; }}
        .hero {{ padding: 100px 20px 48px; }}
        .pipeline {{ gap: 4px; }}
        .pipe-node {{ padding: 6px 10px; font-size: 11px; }}
        .options {{ padding: 0 20px 40px; }}
        .access-section {{ padding: 0 20px 60px; }}
      }}
    </style>
  </head>
  <body>

    <nav class="nav">
      <a class="nav-brand" href="/demo-access">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
          <path d="M2 12h20"/>
        </svg>
        Ovidius
      </a>
      <span class="nav-tag">Senior AI Builder &mdash; Qualification Project</span>
    </nav>

    <!-- Hero -->
    <section class="hero">
      <div class="hero-badge"><span class="dot"></span> Live Demo</div>
      <h1>Production-grade <span>documentation QA</span> on the Anthropic stack</h1>
      <p>
        A 6-stage retrieval pipeline that answers IRS tax questions with cited sources,
        observable reasoning, and measurable quality &mdash; delivered through three interfaces.
      </p>
    </section>

    <!-- Pipeline -->
    <section class="pipeline-section">
      <div class="pipeline-label">Retrieval Pipeline</div>
      <div class="pipeline">
        <div class="pipe-step">
          <div class="pipe-node">Classify</div>
          <span class="pipe-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8h10m-3-3l3 3-3 3"/></svg></span>
        </div>
        <div class="pipe-step">
          <div class="pipe-node">Hybrid Search</div>
          <span class="pipe-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8h10m-3-3l3 3-3 3"/></svg></span>
        </div>
        <div class="pipe-step">
          <div class="pipe-node">Rerank</div>
          <span class="pipe-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8h10m-3-3l3 3-3 3"/></svg></span>
        </div>
        <div class="pipe-step">
          <div class="pipe-node">Corrective RAG</div>
          <span class="pipe-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8h10m-3-3l3 3-3 3"/></svg></span>
        </div>
        <div class="pipe-step">
          <div class="pipe-node">Parent Expand</div>
          <span class="pipe-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 8h10m-3-3l3 3-3 3"/></svg></span>
        </div>
        <div class="pipe-step">
          <div class="pipe-node">Generate + Cite</div>
        </div>
      </div>
    </section>

    <!-- Three Options -->
    <section class="options">
      <div class="opt-card">
        <div class="opt-letter opt-a">A</div>
        <h3>Managed Agent</h3>
        <p>Multi-turn Claude agent with 4 custom tools: knowledge base search, document sections, source comparison, and tax calculations.</p>
        <div class="opt-detail">POST /agent/chat/stream</div>
      </div>
      <div class="opt-card">
        <div class="opt-letter opt-b">B</div>
        <h3>Copilot Skill</h3>
        <p>Claude Code slash command and interactive CLI adapter. Ask tax questions from your terminal without leaving your workflow.</p>
        <div class="opt-detail">/project:tax-qa</div>
      </div>
      <div class="opt-card">
        <div class="opt-letter opt-c">C</div>
        <h3>MCP Server</h3>
        <p>Two tools over stdio transport &mdash; plug into Claude Desktop or any MCP client for instant knowledge base access.</p>
        <div class="opt-detail">kb_search &middot; kb_answer</div>
      </div>
    </section>

    <!-- Tech Stack -->
    <div class="tech-strip">
      <span class="tech-chip">Claude Sonnet</span>
      <span class="tech-chip">Voyage-3</span>
      <span class="tech-chip">pgvector</span>
      <span class="tech-chip">FlashRank</span>
      <span class="tech-chip">FastAPI</span>
      <span class="tech-chip">React 19</span>
      <span class="tech-chip">OpenTelemetry</span>
      <span class="tech-chip">RAGAS</span>
    </div>

    <!-- Access Code -->
    <section class="access-section">
      <div class="access-card">
        <div class="lock-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
        </div>
        <h2>Enter the demo</h2>
        <p class="subtitle">Use the access code provided in your email</p>
        {error_html}
        <form method="post" action="/demo-access">
          <input id="access_code" type="text" name="access_code" placeholder="Access code" autocomplete="off" spellcheck="false" required />
          <button type="submit">Open Dashboard</button>
        </form>
        <p class="footer-note">This demo is private. The code was included in the submission email.</p>
      </div>
    </section>

    <footer class="foot">
      Built by Syam Metta &middot; Senior AI Builder Qualification &middot;
      <a href="https://github.com" target="_blank">View Source</a>
    </footer>

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

        cached = await get_cached_response(req.question)
        if cached:
            span.set_attribute("cache_hit", True)
            cached_ms = round((time.perf_counter() - total_start) * 1000, 1)
            record_request_latency(cached_ms, interface="api")
            return QAResponse(
                answer=cached["answer"],
                citations=[CitationResponse(**c) for c in cached.get("citations", [])],
                confidence=cached.get("confidence", ""),
                retrieval_method="cache",
                chunks_used=cached.get("chunks_used", 0),
                parent_chunks_used=cached.get("parent_chunks_used", 0),
                pipeline=[PipelineStep(step="cache_hit", duration_ms=cached_ms)],
                total_ms=cached_ms,
                trace_id=trace_id,
            )

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

        await cache_response(req.question, {
            "answer": result.answer,
            "citations": [{"index": c.index, "source_url": c.source_url, "source_title": c.source_title} for c in result.citations],
            "confidence": result.confidence,
            "retrieval_method": result.retrieval_method,
            "chunks_used": result.chunks_used,
            "parent_chunks_used": result.parent_chunks_used,
        })

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
        cache_info = await get_cache_stats()
        return {
            "status": "ok" if not startup_db_error else "degraded",
            "child_chunks": child_count,
            "parent_chunks": parent_count,
            "startup_db_error": startup_db_error,
            "cache": cache_info,
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
