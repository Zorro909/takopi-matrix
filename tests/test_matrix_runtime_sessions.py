"""Tests for Matrix runtime session helper logic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from takopi.api import ResumeToken
from takopi_matrix.bridge.config import MatrixBridgeConfig
from takopi_matrix.bridge.runtime import (
    _SessionScope,
    _lookup_session_resume,
    _store_session_resume,
)


@pytest.mark.anyio
async def test_lookup_uses_thread_store_for_thread_messages() -> None:
    thread_store = AsyncMock()
    expected = ResumeToken(engine="codex", value="thread-token")
    thread_store.get_session_resume.return_value = expected
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            session_mode="chat",
            thread_state=thread_store,
            chat_sessions=AsyncMock(),
        ),
    )
    scope = _SessionScope(
        room_id="!room:example.org",
        sender="@user:example.org",
        thread_root_event_id="$thread:example.org",
    )

    found = await _lookup_session_resume(cfg=cfg, scope=scope, engine="codex")
    assert found == expected
    thread_store.get_session_resume.assert_awaited_once_with(
        "!room:example.org", "$thread:example.org", "codex"
    )


@pytest.mark.anyio
async def test_lookup_uses_chat_store_for_non_thread_messages() -> None:
    chat_store = AsyncMock()
    expected = ResumeToken(engine="codex", value="chat-token")
    chat_store.get_session_resume.return_value = expected
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            session_mode="chat",
            thread_state=AsyncMock(),
            chat_sessions=chat_store,
        ),
    )
    scope = _SessionScope(
        room_id="!room:example.org",
        sender="@user:example.org",
        thread_root_event_id=None,
    )

    found = await _lookup_session_resume(cfg=cfg, scope=scope, engine="codex")
    assert found == expected
    chat_store.get_session_resume.assert_awaited_once_with(
        "!room:example.org", "@user:example.org", "codex"
    )


@pytest.mark.anyio
async def test_lookup_disabled_in_stateless_mode() -> None:
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            session_mode="stateless",
            thread_state=AsyncMock(),
            chat_sessions=AsyncMock(),
        ),
    )
    scope = _SessionScope(
        room_id="!room:example.org",
        sender="@user:example.org",
        thread_root_event_id="$thread:example.org",
    )

    found = await _lookup_session_resume(cfg=cfg, scope=scope, engine="codex")
    assert found is None


@pytest.mark.anyio
async def test_store_persists_to_thread_store_for_thread_messages() -> None:
    thread_store = AsyncMock()
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            session_mode="chat",
            thread_state=thread_store,
            chat_sessions=AsyncMock(),
        ),
    )
    scope = _SessionScope(
        room_id="!room:example.org",
        sender="@user:example.org",
        thread_root_event_id="$thread:example.org",
    )
    token = ResumeToken(engine="codex", value="thread-token")

    await _store_session_resume(cfg=cfg, scope=scope, token=token)
    thread_store.set_session_resume.assert_awaited_once_with(
        "!room:example.org", "$thread:example.org", token
    )


@pytest.mark.anyio
async def test_store_persists_to_chat_store_for_non_thread_messages() -> None:
    chat_store = AsyncMock()
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            session_mode="chat",
            thread_state=AsyncMock(),
            chat_sessions=chat_store,
        ),
    )
    scope = _SessionScope(
        room_id="!room:example.org",
        sender="@user:example.org",
        thread_root_event_id=None,
    )
    token = ResumeToken(engine="codex", value="chat-token")

    await _store_session_resume(cfg=cfg, scope=scope, token=token)
    chat_store.set_session_resume.assert_awaited_once_with(
        "!room:example.org", "@user:example.org", token
    )
