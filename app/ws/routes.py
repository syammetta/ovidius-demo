"""WebSocket endpoint — real-time pipeline visibility for the dashboard.

Clients connect to /ws/qa and send a question. The server streams back
events at every pipeline stage so the UI can render a live "glass box"
view of retrieval → rerank → corrective → parent fetch → generation.
"""

import asyncio
import time
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import anthropic

from app.config import settings
from app.agent.session import Message, create_session, load_session, save_session
from app.retrieval.context_builder import retrieve, ProgressCallback
from app.generation.answerer import SYSTEM_PROMPT, LOW_CONFIDENCE_ADDENDUM
from app.retrieval.corrective import RetrievalConfidence
from app.telemetry import (
    get_tracer, get_current_trace_id, get_collector,
    record_request_latency,
)
from app.middleware.query_logger import log_query, QueryLogEntry

logger = logging.getLogger(__name__)

router = APIRouter()


def _make_progress_sender(ws: WebSocket) -> ProgressCallback:
    """Create a progress callback that sends events over the WebSocket."""
    async def send_progress(stage: str, status: str, detail: dict) -> None:
        try:
            await ws.send_json({
                "type": "stage",
                "stage": stage,
                "status": status,
                **detail,
            })
        except Exception:
            pass
    return send_progress


async def _resolve_session(session_id: str | None):
    if session_id:
        session = await load_session(session_id)
        if session:
            return session
    return await create_session()


@router.websocket("/ws/qa")
async def ws_qa(websocket: WebSocket):
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            question = data.get("question", "").strip()
            if not question:
                await websocket.send_json({"type": "error", "message": "Empty question"})
                continue

            mode = data.get("mode", "direct")
            session_id = data.get("session_id")

            if mode == "agent":
                await _handle_agent_mode(websocket, question, data, session_id=session_id)
            else:
                await _handle_direct_mode(websocket, question, session_id=session_id)

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.warning("WebSocket error", exc_info=True)


async def _handle_direct_mode(ws: WebSocket, question: str, session_id: str | None = None):
    """Direct QA: retrieve with live progress, then generate."""
    tracer = get_tracer("ws")
    t_start = time.perf_counter()
    progress = _make_progress_sender(ws)
    session = await _resolve_session(session_id)
    session.messages.append(Message(role="user", content=question))
    await save_session(session)

    with tracer.start_as_current_span("ws_qa_request") as span:
        trace_id = get_current_trace_id()
        span.set_attribute("question", question[:500])
        span.set_attribute("interface", "ws")
        span.set_attribute("session_id", session.session_id)

        await ws.send_json({"type": "start", "trace_id": trace_id, "session_id": session.session_id})

        # Retrieval with live stage events
        retrieval_result = await retrieve(question, on_progress=progress)
        retrieval_ms = round((time.perf_counter() - t_start) * 1000, 1)

        doc_type_counts: dict[str, int] = {}
        for c in retrieval_result.children:
            doc_type_counts[c.document_type] = doc_type_counts.get(c.document_type, 0) + 1

        cls = retrieval_result.classification
        strat = retrieval_result.strategy

        await ws.send_json({
            "type": "retrieval_complete",
            "confidence": retrieval_result.corrective.confidence.value,
            "chunks": len(retrieval_result.children),
            "parents": len(retrieval_result.parent_contents),
            "filtered": f"{retrieval_result.corrective.filtered_count}/{retrieval_result.corrective.original_count}",
            "relevance_ratio": round(
                retrieval_result.corrective.filtered_count / max(retrieval_result.corrective.original_count, 1), 2
            ),
            "retry_performed": retrieval_result.retry_performed,
            "transformed_query": retrieval_result.corrective.transformed_query,
            "doc_types": doc_type_counts,
            "duration_ms": retrieval_ms,
            "classification": {
                "intent": cls.intent,
                "topics": cls.topics,
                "doc_types": cls.doc_types,
                "sections": cls.sections,
                "reasoning": cls.reasoning,
            } if cls else None,
            "strategy": {
                "name": strat.name,
                "description": strat.description,
                "top_n": strat.top_n,
                "top_k": strat.top_k,
                "metadata_boost": strat.metadata_boost,
            } if strat else None,
            "sources": [
                {
                    "title": c.source_title,
                    "url": c.source_url,
                    "type": c.document_type,
                    "method": c.retrieval_method,
                    "parent_id": c.parent_id,
                }
                for c in retrieval_result.children
            ],
        })

        # Generation with streaming
        await progress("generation", "running", {})
        t_gen = time.perf_counter()

        confidence = retrieval_result.corrective.confidence
        children = retrieval_result.children
        parent_contents = retrieval_result.parent_contents

        context_parts = []
        seen_parents = set()
        for i, child in enumerate(children):
            parent_content = parent_contents.get(child.parent_id)
            if parent_content and child.parent_id not in seen_parents:
                context_parts.append(
                    f"[{i + 1}] (Source: {child.source_title} | Type: {child.document_type})\n"
                    f"{parent_content}"
                )
                seen_parents.add(child.parent_id)
            else:
                context_parts.append(
                    f"[{i + 1}] (Source: {child.source_title} | Type: {child.document_type})\n"
                    f"{child.contextual_content or child.content}"
                )

        context = "\n\n---\n\n".join(context_parts)
        system = SYSTEM_PROMPT
        if confidence == RetrievalConfidence.LOW_CONFIDENCE:
            system += LOW_CONFIDENCE_ADDENDUM

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        full_text = ""
        with client.messages.stream(
            model=settings.generation_model,
            max_tokens=1024,
            system=system,
            messages=[{
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion: {question}",
            }],
        ) as stream:
            for text in stream.text_stream:
                full_text += text
                await ws.send_json({"type": "text_delta", "text": text})

        gen_ms = round((time.perf_counter() - t_gen) * 1000, 1)
        total_ms = round((time.perf_counter() - t_start) * 1000, 1)

        await progress("generation", "complete", {"duration_ms": gen_ms})

        span.set_attribute("total_ms", total_ms)
        span.set_attribute("confidence", confidence.value)
        record_request_latency(total_ms, interface="ws")

        citations = [
            {"index": i + 1, "source_url": c.source_url, "source_title": c.source_title}
            for i, c in enumerate(children)
        ]

        # Send trace waterfall
        collector = get_collector()
        trace_data = collector.get_trace(trace_id) if collector and trace_id else None

        await ws.send_json({
            "type": "done",
            "answer": full_text,
            "citations": citations,
            "confidence": confidence.value,
            "retrieval_method": "+".join({c.retrieval_method for c in children}),
            "chunks_used": len(children),
            "parent_chunks_used": len(seen_parents),
            "trace_id": trace_id,
            "total_ms": total_ms,
            "retrieval_ms": retrieval_ms,
            "generation_ms": gen_ms,
            "trace": trace_data,
            "session_id": session.session_id,
        })

        session.messages.append(Message(role="assistant", content=full_text))
        await save_session(session)

        try:
            await log_query(QueryLogEntry(
                question=question,
                answer=full_text[:1000],
                citations=citations,
                confidence=confidence.value,
                retrieval_method="+".join({c.retrieval_method for c in children}),
                chunks_used=len(children),
                parent_chunks_used=len(seen_parents),
                latency_ms=total_ms,
                retrieval_ms=retrieval_ms,
                generation_ms=gen_ms,
                trace_id=trace_id,
                session_id=session.session_id,
                interface="ws",
            ))
        except Exception:
            pass


async def _handle_agent_mode(ws: WebSocket, question: str, data: dict, session_id: str | None = None):
    """Agent mode: multi-turn tool use with live pipeline events."""
    from app.agent.tools import TOOL_DEFINITIONS, handle_tool_call, ToolCache
    from app.agent.prompts import AGENT_SYSTEM

    tracer = get_tracer("ws")
    t_start = time.perf_counter()
    max_turns = 10

    with tracer.start_as_current_span("ws_agent_request") as span:
        trace_id = get_current_trace_id()
        span.set_attribute("question", question[:500])
        span.set_attribute("interface", "ws_agent")
        session = await _resolve_session(session_id)
        span.set_attribute("session_id", session.session_id)

        await ws.send_json({"type": "start", "trace_id": trace_id, "mode": "agent", "session_id": session.session_id})

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        tool_cache = ToolCache()
        messages = [{"role": m.role, "content": m.content} for m in session.messages]
        messages.append({"role": "user", "content": question})
        tool_calls_info = []
        turn_count = 0

        create_kwargs = {
            "model": settings.generation_model,
            "max_tokens": 4096,
            "system": AGENT_SYSTEM,
            "tools": TOOL_DEFINITIONS,
        }

        response = await asyncio.to_thread(
            client.messages.create,
            **create_kwargs,
            messages=messages,
        )
        turn_count += 1

        while response.stop_reason == "tool_use" and turn_count <= max_turns:
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    await ws.send_json({
                        "type": "tool_call",
                        "tool_name": block.name,
                        "tool_input": block.input,
                    })

                    t_tool = time.perf_counter()
                    result = await handle_tool_call(block.name, block.input, cache=tool_cache)
                    tool_ms = round((time.perf_counter() - t_tool) * 1000, 1)

                    tool_calls_info.append({
                        "tool_name": block.name,
                        "duration_ms": tool_ms,
                    })

                    await ws.send_json({
                        "type": "tool_result",
                        "tool_name": block.name,
                        "result_preview": result[:800],
                        "duration_ms": tool_ms,
                    })

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

            response = await asyncio.to_thread(
                client.messages.create,
                **create_kwargs,
                messages=messages,
            )
            turn_count += 1

        # Stream final response text
        reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                reply += block.text
                await ws.send_json({"type": "text_delta", "text": block.text})

        total_ms = round((time.perf_counter() - t_start) * 1000, 1)
        span.set_attribute("total_ms", total_ms)
        span.set_attribute("tool_calls", len(tool_calls_info))
        record_request_latency(total_ms, interface="ws_agent")

        collector = get_collector()
        trace_data = collector.get_trace(trace_id) if collector and trace_id else None

        await ws.send_json({
            "type": "done",
            "answer": reply,
            "tool_calls": tool_calls_info,
            "turn_count": turn_count,
            "trace_id": trace_id,
            "total_ms": total_ms,
            "trace": trace_data,
            "session_id": session.session_id,
        })

        session.messages.append(Message(role="user", content=question))
        session.messages.append(Message(role="assistant", content=reply))
        await save_session(session)

        try:
            await log_query(QueryLogEntry(
                question=question,
                answer=reply[:1000],
                latency_ms=total_ms,
                trace_id=trace_id,
                session_id=session.session_id,
                interface="ws_agent",
            ))
        except Exception:
            pass
