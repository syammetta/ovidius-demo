"""Tests for agent session management."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.agent.session import create_session, load_session, save_session, Session, Message


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_creates_session_with_uuid(self, mock_db_pool):
        session = await create_session()
        assert session.session_id is not None
        assert len(session.session_id) == 36  # UUID format
        assert session.messages == []

    @pytest.mark.asyncio
    async def test_inserts_into_db(self, mock_db_pool):
        await create_session()
        mock_db_pool.execute.assert_called_once()
        call_args = mock_db_pool.execute.call_args
        assert "INSERT INTO sessions" in call_args[0][0]


class TestLoadSession:
    @pytest.mark.asyncio
    async def test_loads_existing_session(self, mock_db_pool):
        mock_db_pool.fetchrow.return_value = {
            "messages": json.dumps([
                {"role": "user", "content": "What is the standard deduction?"},
                {"role": "assistant", "content": "The standard deduction is $15,000 [1]."},
            ])
        }

        session = await load_session("test-session-id")
        assert session is not None
        assert session.session_id == "test-session-id"
        assert len(session.messages) == 2
        assert session.messages[0].role == "user"
        assert session.messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, mock_db_pool):
        mock_db_pool.fetchrow.return_value = None
        session = await load_session("nonexistent-id")
        assert session is None

    @pytest.mark.asyncio
    async def test_loads_empty_messages(self, mock_db_pool):
        mock_db_pool.fetchrow.return_value = {"messages": "[]"}
        session = await load_session("empty-session")
        assert session is not None
        assert session.messages == []


class TestSaveSession:
    @pytest.mark.asyncio
    async def test_saves_messages(self, mock_db_pool):
        session = Session(
            session_id="test-id",
            messages=[
                Message(role="user", content="Hello"),
                Message(role="assistant", content="Hi there"),
            ],
        )
        await save_session(session)

        mock_db_pool.execute.assert_called_once()
        call_args = mock_db_pool.execute.call_args
        assert "UPDATE sessions" in call_args[0][0]

        saved_json = call_args[0][1]
        saved_messages = json.loads(saved_json)
        assert len(saved_messages) == 2
        assert saved_messages[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_saves_empty_messages(self, mock_db_pool):
        session = Session(session_id="test-id", messages=[])
        await save_session(session)

        call_args = mock_db_pool.execute.call_args
        saved_json = call_args[0][1]
        assert json.loads(saved_json) == []
