"""Tests for built-in Matrix commands handled by transport."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from takopi.api import RunContext

from matrix_fixtures import make_matrix_message
import takopi_matrix.bridge.commands.builtin as builtin_commands
from takopi_matrix.bridge.commands.builtin import handle_builtin_command


class _FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send(self, *, channel_id, message, options=None):
        self.calls.append(
            {
                "channel_id": channel_id,
                "text": message.text,
                "options": options,
            }
        )
        return None


def _build_cfg() -> tuple[Any, _FakeTransport]:
    transport = _FakeTransport()
    runtime = SimpleNamespace(
        normalize_project_key=lambda token: token.lower(),
        project_alias_for_key=lambda key: key,
        engine_ids=("codex", "claude"),
        default_engine="codex",
        project_default_engine=lambda context: None,
        project_aliases=lambda: ("website", "app"),
        config_path=None,
        resolve_run_cwd=lambda context: None,
        resolve_message=lambda **kwargs: SimpleNamespace(
            context=kwargs.get("ambient_context"), context_source="ambient"
        ),
    )
    room_prefs = AsyncMock()
    room_prefs.get_default_engine = AsyncMock(return_value=None)
    room_prefs.get_engine_override = AsyncMock(return_value=None)
    room_prefs.get_trigger_mode = AsyncMock(return_value=None)
    thread_state = AsyncMock()
    thread_state.get_default_engine = AsyncMock(return_value=None)
    thread_state.get_engine_override = AsyncMock(return_value=None)
    thread_state.get_trigger_mode = AsyncMock(return_value=None)
    client = AsyncMock()
    client.is_direct_room = AsyncMock(return_value=True)
    client.is_room_admin = AsyncMock(return_value=True)
    client.send_file = AsyncMock(return_value={"event_id": "$file"})
    cfg = SimpleNamespace(
        exec_cfg=SimpleNamespace(transport=transport),
        runtime=runtime,
        room_prefs=room_prefs,
        thread_state=thread_state,
        chat_sessions=AsyncMock(),
        room_project_map=None,
        file_download=SimpleNamespace(max_size_bytes=50 * 1024 * 1024),
        client=client,
        user_allowlist=None,
    )
    return cfg, transport


@pytest.mark.anyio
async def test_ctx_set_uses_room_scope_outside_thread() -> None:
    cfg, _ = _build_cfg()
    msg = make_matrix_message(text="/ctx set website @feat-green")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="ctx",
        args_text="set website @feat-green",
        ambient_context=None,
    )

    assert handled is True
    cfg.room_prefs.set_context.assert_awaited_once()
    room_id, context = cfg.room_prefs.set_context.await_args.args
    assert room_id == msg.room_id
    assert context == RunContext(project="website", branch="feat-green")


@pytest.mark.anyio
async def test_ctx_set_uses_thread_scope_in_thread() -> None:
    cfg, _ = _build_cfg()
    msg = make_matrix_message(
        text="/ctx set app @feat-font",
        thread_root_event_id="$thread-root",
    )

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="ctx",
        args_text="set app @feat-font",
        ambient_context=None,
    )

    assert handled is True
    cfg.thread_state.set_context.assert_awaited_once()
    room_id, thread_root, context = cfg.thread_state.set_context.await_args.args
    assert room_id == msg.room_id
    assert thread_root == "$thread-root"
    assert context == RunContext(project="app", branch="feat-font")


@pytest.mark.anyio
async def test_new_clears_room_sender_sessions_outside_thread() -> None:
    cfg, _ = _build_cfg()
    msg = make_matrix_message(text="/new", sender="@alice:example.org")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="new",
        args_text="",
        ambient_context=None,
    )

    assert handled is True
    cfg.chat_sessions.clear_sessions.assert_awaited_once_with(
        msg.room_id,
        msg.sender,
    )


@pytest.mark.anyio
async def test_new_clears_thread_sessions_in_thread() -> None:
    cfg, _ = _build_cfg()
    msg = make_matrix_message(text="/new", thread_root_event_id="$thread-root")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="new",
        args_text="",
        ambient_context=None,
    )

    assert handled is True
    cfg.thread_state.clear_sessions.assert_awaited_once_with(
        msg.room_id,
        "$thread-root",
    )


@pytest.mark.anyio
async def test_agent_set_updates_scope_default_engine() -> None:
    cfg, _ = _build_cfg()
    msg = make_matrix_message(text="/agent set codex")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="agent",
        args_text="set codex",
        ambient_context=None,
    )

    assert handled is True
    cfg.room_prefs.set_default_engine.assert_awaited_once_with(msg.room_id, "codex")


@pytest.mark.anyio
async def test_trigger_mentions_sets_thread_override() -> None:
    cfg, _ = _build_cfg()
    msg = make_matrix_message(text="/trigger mentions", thread_root_event_id="$thread")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="trigger",
        args_text="mentions",
        ambient_context=None,
    )

    assert handled is True
    cfg.thread_state.set_trigger_mode.assert_awaited_once_with(
        msg.room_id, "$thread", "mentions"
    )


@pytest.mark.anyio
async def test_file_put_without_attachment_returns_usage() -> None:
    cfg, transport = _build_cfg()
    msg = make_matrix_message(text="/file put uploads/")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="file",
        args_text="put uploads/",
        ambient_context=None,
    )

    assert handled is True
    assert transport.calls
    assert "usage: `/file put <path>`" in str(transport.calls[-1]["text"])


@pytest.mark.anyio
async def test_agent_set_denied_without_admin_or_private() -> None:
    cfg, transport = _build_cfg()
    cfg.client.is_direct_room.return_value = False
    cfg.client.is_room_admin.return_value = False
    msg = make_matrix_message(text="/agent set codex")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="agent",
        args_text="set codex",
        ambient_context=None,
    )

    assert handled is True
    cfg.room_prefs.set_default_engine.assert_not_called()
    assert "restricted to room admins" in str(transport.calls[-1]["text"])


@pytest.mark.anyio
async def test_reload_requests_process_restart(monkeypatch) -> None:
    cfg, transport = _build_cfg()
    restarted = False

    def _fake_restart() -> None:
        nonlocal restarted
        restarted = True

    monkeypatch.setattr(builtin_commands, "_request_process_restart", _fake_restart)
    msg = make_matrix_message(text="/reload")

    handled = await handle_builtin_command(
        cfg,
        msg,
        command_id="reload",
        args_text="",
        ambient_context=None,
    )

    assert handled is True
    assert restarted is True
    assert "restarting takopi process now" in str(transport.calls[-1]["text"])
