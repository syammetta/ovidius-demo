"""FastAPI routes for the multi-turn agent with streaming, extended thinking, and tracing."""

import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import anthropic

from app.config import settings
from app.agent.session import create_session, load_session, save_session, Message
from app.agent.tools import TOOL_DEFINITIONS, handle_tool_call, ToolCache
from app.agent.prompts import AGENT_SYSTEM
from app.telemetry import (
    get_tracer, get_current_trace_id, record_tool_calls, record_request_latency,
)
from app.middleware.query_logger import log_query, QueryLogEntry

router = APIRouter(prefix="/agent", tags=["agent"])

MAX_TOOL_TURNS = 10
MAX_THINKING_BUDGET = 8192


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    enable_thinking: bool = False
    stream: bool = False


class ToolCallInfo(BaseModel):
    tool_name: str
    tool_input: dict
    result_preview: str
    duration_ms: float = 0


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    tool_calls: list[ToolCallInfo]
    thinking: str | None = None
    trace_id: str | None = None
    turn_count: int = 1
    latency_ms: float = 0


# ---------------------------------------------------------------------------
# Conversation management
# ---------------------------------------------------------------------------

def _build_api_messages(messages: list[Message]) -> list[dict]:
    """Convert session messages to Anthropic API format."""
    return [{"role": m.role, "content": m.content} for m in messages]


async def _summarize_if_needed(
    messages: list[Message],
    keep_recent: int = 6,
) -> list[Message]:
    """Compress old messages if conversation exceeds context budget."""
    if len(messages) <= keep_recent * 2:
        return messages

    old = messages[:-keep_recent * 2]
    recent = messages[-keep_recent * 2:]

    old_text = "\n".join(
        f"{m.role}: {m.content if isinstance(m.content, str) else '[tool interaction]'}"
        for m in old
    )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = await asyncio.to_thread(
        client.messages.create,
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": (
                "Summarize this conversation history in 2-3 sentences, "
                "preserving key facts, decisions, and source citations:\n\n"
                + old_text[:8000]
            ),
        }],
    )
    summary = resp.content[0].text

    return [
        Message(role="user", content=f"[Conversation summary: {summary}]"),
        Message(role="assistant", content="Understood, I have the context from our earlier discussion."),
        *recent,
    ]


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------

async def _run_agent_turn(
    session,
    user_message: str,
    enable_thinking: bool = False,
) -> tuple[str, list[ToolCallInfo], str | None, int]:
    """Execute one full agent turn (possibly multiple LLM calls with tool use).

    Returns (reply_text, tool_calls_info, thinking_text, turn_count).
    """
    tracer = get_tracer("agent")
    tool_cache = ToolCache()
    tool_calls_info = []
    thinking_parts = []
    turn_count = 0

    session.messages.append(Message(role="user", content=user_message))
    session.messages = await _summarize_if_needed(session.messages)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    api_messages = _build_api_messages(session.messages)

    create_kwargs = {
        "model": settings.generation_model,
        "max_tokens": 4096,
        "system": AGENT_SYSTEM,
        "tools": TOOL_DEFINITIONS,
        "messages": api_messages,
    }

    if enable_thinking:
        create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": MAX_THINKING_BUDGET}

    with tracer.start_as_current_span("agent_turn") as turn_span:
        turn_span.set_attribute("session_id", session.session_id)
        turn_span.set_attribute("user_message", user_message[:500])

        response = await asyncio.to_thread(client.messages.create, **create_kwargs)
        turn_count += 1

        while response.stop_reason == "tool_use" and turn_count <= MAX_TOOL_TURNS:
            tool_blocks = [b for b in response.content if b.type == "tool_use"]
            thinking_blocks = [b for b in response.content if getattr(b, "type", None) == "thinking"]

            for tb in thinking_blocks:
                thinking_parts.append(tb.thinking)

            tool_results = []
            for block in tool_blocks:
                with tracer.start_as_current_span(f"tool_call:{block.name}") as tool_span:
                    tool_span.set_attribute("tool.name", block.name)
                    tool_span.set_attribute("tool.input", json.dumps(block.input)[:500])

                    t0 = time.perf_counter()
                    result = await handle_tool_call(block.name, block.input, cache=tool_cache)
                    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

                    tool_span.set_attribute("tool.duration_ms", elapsed_ms)
                    tool_span.set_attribute("tool.result_length", len(result))

                tool_calls_info.append(ToolCallInfo(
                    tool_name=block.name,
                    tool_input=block.input,
                    result_preview=result[:300],
                    duration_ms=elapsed_ms,
                ))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            api_messages.append({"role": "assistant", "content": response.content})
            api_messages.append({"role": "user", "content": tool_results})

            response = await asyncio.to_thread(client.messages.create, **create_kwargs | {"messages": api_messages})
            turn_count += 1

        # Extract final thinking blocks
        for block in response.content:
            if getattr(block, "type", None) == "thinking":
                thinking_parts.append(block.thinking)

        reply = "".join(b.text for b in response.content if hasattr(b, "text"))

        turn_span.set_attribute("turn_count", turn_count)
        turn_span.set_attribute("tool_call_count", len(tool_calls_info))
        turn_span.set_attribute("reply_length", len(reply))

        record_tool_calls(len(tool_calls_info))

    session.messages.append(Message(role="assistant", content=reply))
    await save_session(session)

    thinking_text = "\n\n".join(thinking_parts) if thinking_parts else None
    return reply, tool_calls_info, thinking_text, turn_count


# ---------------------------------------------------------------------------
# Non-streaming endpoint
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    tracer = get_tracer("agent")
    t0 = time.perf_counter()

    with tracer.start_as_current_span("agent_request") as span:
        trace_id = get_current_trace_id()
        span.set_attribute("interface", "agent")

        if req.session_id:
            session = await load_session(req.session_id)
            if not session:
                session = await create_session()
        else:
            session = await create_session()

        span.set_attribute("session_id", session.session_id)

        reply, tool_calls, thinking, turn_count = await _run_agent_turn(
            session, req.message, enable_thinking=req.enable_thinking,
        )

        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        record_request_latency(latency_ms, interface="agent")

        asyncio.create_task(log_query(QueryLogEntry(
            question=req.message,
            answer=reply[:1000],
            confidence=None,
            latency_ms=latency_ms,
            trace_id=trace_id,
            session_id=session.session_id,
            interface="agent",
        )))

        return ChatResponse(
            reply=reply,
            session_id=session.session_id,
            tool_calls=tool_calls,
            thinking=thinking,
            trace_id=trace_id,
            turn_count=turn_count,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# Streaming endpoint (SSE)
# ---------------------------------------------------------------------------

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Stream agent responses via Server-Sent Events.

    Event types:
      session_id  — emitted once at start
      thinking    — reasoning blocks (if enabled)
      tool_call   — tool name + input when a tool is invoked
      tool_result — tool result preview
      text_delta  — incremental text from the final response
      done        — final metadata (trace_id, tool count, latency)
      error       — if something goes wrong
    """
    tracer = get_tracer("agent")

    async def event_generator() -> AsyncGenerator[str, None]:
        t0 = time.perf_counter()

        with tracer.start_as_current_span("agent_stream_request") as span:
            trace_id = get_current_trace_id()
            span.set_attribute("interface", "agent_stream")

            try:
                if req.session_id:
                    session = await load_session(req.session_id)
                    if not session:
                        session = await create_session()
                else:
                    session = await create_session()

                yield _sse("session_id", {"session_id": session.session_id})

                session.messages.append(Message(role="user", content=req.message))
                session.messages = await _summarize_if_needed(session.messages)

                client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
                api_messages = _build_api_messages(session.messages)

                create_kwargs = {
                    "model": settings.generation_model,
                    "max_tokens": 4096,
                    "system": AGENT_SYSTEM,
                    "tools": TOOL_DEFINITIONS,
                    "messages": api_messages,
                }
                if req.enable_thinking:
                    create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": MAX_THINKING_BUDGET}

                tool_calls_info = []
                tool_cache = ToolCache()
                turn_count = 0

                response = await asyncio.to_thread(client.messages.create, **create_kwargs)
                turn_count += 1

                while response.stop_reason == "tool_use" and turn_count <= MAX_TOOL_TURNS:
                    tool_results = []
                    for block in response.content:
                        if getattr(block, "type", None) == "thinking":
                            yield _sse("thinking", {"text": block.thinking})
                        elif block.type == "tool_use":
                            yield _sse("tool_call", {
                                "tool_name": block.name,
                                "tool_input": block.input,
                            })

                            t_tool = time.perf_counter()
                            result = await handle_tool_call(block.name, block.input, cache=tool_cache)
                            tool_ms = round((time.perf_counter() - t_tool) * 1000, 1)

                            tool_calls_info.append(ToolCallInfo(
                                tool_name=block.name,
                                tool_input=block.input,
                                result_preview=result[:300],
                                duration_ms=tool_ms,
                            ))
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            })
                            yield _sse("tool_result", {
                                "tool_name": block.name,
                                "result_preview": result[:500],
                                "duration_ms": tool_ms,
                            })

                    api_messages.append({"role": "assistant", "content": response.content})
                    api_messages.append({"role": "user", "content": tool_results})

                    response = await asyncio.to_thread(
                        client.messages.create,
                        **create_kwargs | {"messages": api_messages},
                    )
                    turn_count += 1

                # Final response — emit text and thinking
                reply_parts = []
                for block in response.content:
                    if getattr(block, "type", None) == "thinking":
                        yield _sse("thinking", {"text": block.thinking})
                    elif hasattr(block, "text"):
                        yield _sse("text_delta", {"text": block.text})
                        reply_parts.append(block.text)

                reply = "".join(reply_parts)
                session.messages.append(Message(role="assistant", content=reply))
                await save_session(session)

                latency_ms = round((time.perf_counter() - t0) * 1000, 1)

                yield _sse("done", {
                    "trace_id": trace_id,
                    "session_id": session.session_id,
                    "tool_call_count": len(tool_calls_info),
                    "turn_count": turn_count,
                    "latency_ms": latency_ms,
                })

                record_request_latency(latency_ms, interface="agent_stream")
                record_tool_calls(len(tool_calls_info))

                asyncio.create_task(log_query(QueryLogEntry(
                    question=req.message,
                    answer=reply[:1000],
                    latency_ms=latency_ms,
                    trace_id=trace_id,
                    session_id=session.session_id,
                    interface="agent_stream",
                )))

            except Exception:
                yield _sse("error", {"message": "An internal error occurred. Please try again."})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
