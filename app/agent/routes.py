"""FastAPI routes for the managed multi-turn agent."""

from fastapi import APIRouter
from pydantic import BaseModel

import anthropic

from app.config import settings
from app.agent.session import create_session, load_session, save_session, Message
from app.agent.tools import TOOL_DEFINITIONS, handle_tool_call

router = APIRouter(prefix="/agent", tags=["agent"])

AGENT_SYSTEM = """You are a documentation assistant with access to a knowledge base search tool.

Rules:
- Use the search_knowledge_base tool to find relevant information before answering.
- Always cite your sources using [1], [2], etc. markers matching the search results.
- If you can't find the answer in the knowledge base, say so.
- For follow-up questions, decide whether you need to search again or can answer from prior context.
- Be concise and direct."""


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ToolCallInfo(BaseModel):
    tool_name: str
    tool_input: dict
    result_preview: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    tool_calls: list[ToolCallInfo]


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if req.session_id:
        session = await load_session(req.session_id)
        if not session:
            session = await create_session()
    else:
        session = await create_session()

    session.messages.append(Message(role="user", content=req.message))

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    api_messages = [{"role": m.role, "content": m.content} for m in session.messages]

    response = client.messages.create(
        model=settings.generation_model,
        max_tokens=1024,
        system=AGENT_SYSTEM,
        tools=TOOL_DEFINITIONS,
        messages=api_messages,
    )

    tool_calls_info = []

    while response.stop_reason == "tool_use":
        tool_blocks = [b for b in response.content if b.type == "tool_use"]
        tool_results = []
        for block in tool_blocks:
            result = await handle_tool_call(block.name, block.input)
            tool_calls_info.append(ToolCallInfo(
                tool_name=block.name,
                tool_input=block.input,
                result_preview=result[:200],
            ))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        api_messages.append({"role": "assistant", "content": response.content})
        api_messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=settings.generation_model,
            max_tokens=1024,
            system=AGENT_SYSTEM,
            tools=TOOL_DEFINITIONS,
            messages=api_messages,
        )

    reply = "".join(b.text for b in response.content if hasattr(b, "text"))

    session.messages.append(Message(role="assistant", content=reply))
    await save_session(session)

    return ChatResponse(
        reply=reply,
        session_id=session.session_id,
        tool_calls=tool_calls_info,
    )
