"""Session management for multi-turn agent conversations — Postgres-backed."""

import json
import uuid
from dataclasses import dataclass, field

from app.db import get_pool


@dataclass
class Message:
    role: str
    content: str


@dataclass
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)


async def create_session() -> Session:
    session = Session(session_id=str(uuid.uuid4()))
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (session_id, messages) VALUES ($1, $2)",
            session.session_id,
            "[]",
        )
    return session


async def load_session(session_id: str) -> Session | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT messages FROM sessions WHERE session_id = $1",
            session_id,
        )
    if not row:
        return None
    messages = [Message(**m) for m in json.loads(row["messages"])]
    return Session(session_id=session_id, messages=messages)


async def save_session(session: Session) -> None:
    data = json.dumps([{"role": m.role, "content": m.content} for m in session.messages])
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET messages = $1, updated_at = now() WHERE session_id = $2",
            data,
            session.session_id,
        )
