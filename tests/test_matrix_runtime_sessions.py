"""Tests for Matrix runtime session helper logic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from takopi.api import ResumeToken, RunContext
from takopi_matrix.bridge.config import MatrixBridgeConfig
from takopi_matrix.bridge.runtime import (
    _SessionScope,
    _lookup_session_resume,
    _resolve_ambient_context,
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


@pytest.mark.anyio
async def test_resolve_ambient_context_prefers_thread_then_room_then_mapping() -> None:
    room_map = SimpleNamespace(
        context_for_room=lambda room_id: RunContext(project="mapped", branch=None)
    )
    room_prefs = AsyncMock()
    room_prefs.get_context.return_value = RunContext(project="room", branch="r1")
    thread_state = AsyncMock()
    thread_state.get_context.return_value = RunContext(project="thread", branch="t1")
    cfg = SimpleNamespace(
        room_project_map=room_map,
        room_prefs=room_prefs,
        thread_state=thread_state,
    )

    context = await _resolve_ambient_context(
        cfg=cfg,
        room_id="!room:example.org",
        thread_root_event_id="$thread",
    )
    assert context == RunContext(project="thread", branch="t1")


@pytest.mark.anyio
async def test_resolve_ambient_context_uses_room_context_outside_thread() -> None:
    room_map = SimpleNamespace(
        context_for_room=lambda room_id: RunContext(project="mapped", branch=None)
    )
    room_prefs = AsyncMock()
    room_prefs.get_context.return_value = RunContext(project="room", branch="main")
    cfg = SimpleNamespace(
        room_project_map=room_map,
        room_prefs=room_prefs,
        thread_state=AsyncMock(),
    )

    context = await _resolve_ambient_context(
        cfg=cfg,
        room_id="!room:example.org",
        thread_root_event_id=None,
    )
    assert context == RunContext(project="room", branch="main")
