"""Tests for command-path session persistence in Matrix runtime."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest
from takopi.api import ResumeToken, RunRequest, ThreadScheduler

from takopi_matrix.bridge.commands.dispatch import dispatch_command
from takopi_matrix.bridge.config import MatrixBridgeConfig
from takopi_matrix.bridge.runtime import _SessionScope, _wrap_on_thread_known
from takopi_matrix.types import MatrixIncomingMessage


class _RuntimeStub:
    allowlist = None
    config_path = None

    def resolve_engine(self, *, engine_override: str | None, context: object) -> str:
        _ = context
        return engine_override or "codex"

    def plugin_config(self, command_id: str) -> dict[str, object]:
        _ = command_id
        return {}


class _BackendRunOne:
    async def handle(self, ctx) -> None:  # noqa: ANN001
        await ctx.executor.run_one(RunRequest(prompt="do x"))


def _make_message(*, thread_root_event_id: str | None) -> MatrixIncomingMessage:
    return MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event:example.org",
        sender="@user:example.org",
        text="/run_cmd",
        thread_root_event_id=thread_root_event_id,
    )


@pytest.mark.anyio
async def test_dispatch_command_persists_chat_session_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = ResumeToken(engine="codex", value="resume-cmd-chat")
    chat_store = AsyncMock()
    thread_store = AsyncMock()
    scheduler = SimpleNamespace(note_thread_known=AsyncMock())
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            runtime=_RuntimeStub(),
            exec_cfg=SimpleNamespace(
                transport=AsyncMock(),
                presenter=None,
                final_notify=True,
            ),
            session_mode="chat",
            chat_sessions=chat_store,
            thread_state=thread_store,
        ),
    )
    msg = _make_message(thread_root_event_id=None)
    scope = _SessionScope(
        room_id=msg.room_id,
        sender=msg.sender,
        thread_root_event_id=None,
    )

    async def _run_engine_fn(**kwargs):  # noqa: ANN003
        on_thread_known = kwargs["on_thread_known"]
        done = anyio.Event()
        await on_thread_known(token, done)

    import takopi_matrix.bridge.commands.dispatch as dispatch_mod

    monkeypatch.setattr(
        dispatch_mod,
        "get_command",
        lambda command_id, allowlist=None, required=False: _BackendRunOne(),
    )
    await dispatch_command(
        cfg,
        msg,
        msg.text,
        "run_cmd",
        "",
        running_tasks={},
        scheduler=cast(ThreadScheduler, scheduler),
        run_engine_fn=_run_engine_fn,
        on_thread_known=_wrap_on_thread_known(
            cfg=cfg,
            scope=scope,
            base_cb=scheduler.note_thread_known,
        ),
    )

    scheduler.note_thread_known.assert_awaited_once()
    chat_store.set_session_resume.assert_awaited_once_with(
        msg.room_id,
        msg.sender,
        token,
    )
    thread_store.set_session_resume.assert_not_called()


@pytest.mark.anyio
async def test_dispatch_command_persists_thread_session_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = ResumeToken(engine="codex", value="resume-cmd-thread")
    chat_store = AsyncMock()
    thread_store = AsyncMock()
    scheduler = SimpleNamespace(note_thread_known=AsyncMock())
    cfg = cast(
        MatrixBridgeConfig,
        SimpleNamespace(
            runtime=_RuntimeStub(),
            exec_cfg=SimpleNamespace(
                transport=AsyncMock(),
                presenter=None,
                final_notify=True,
            ),
            session_mode="chat",
            chat_sessions=chat_store,
            thread_state=thread_store,
        ),
    )
    msg = _make_message(thread_root_event_id="$thread:example.org")
    scope = _SessionScope(
        room_id=msg.room_id,
        sender=msg.sender,
        thread_root_event_id=msg.thread_root_event_id,
    )

    async def _run_engine_fn(**kwargs):  # noqa: ANN003
        on_thread_known = kwargs["on_thread_known"]
        done = anyio.Event()
        await on_thread_known(token, done)

    import takopi_matrix.bridge.commands.dispatch as dispatch_mod

    monkeypatch.setattr(
        dispatch_mod,
        "get_command",
        lambda command_id, allowlist=None, required=False: _BackendRunOne(),
    )
    await dispatch_command(
        cfg,
        msg,
        msg.text,
        "run_cmd",
        "",
        running_tasks={},
        scheduler=cast(ThreadScheduler, scheduler),
        run_engine_fn=_run_engine_fn,
        on_thread_known=_wrap_on_thread_known(
            cfg=cfg,
            scope=scope,
            base_cb=scheduler.note_thread_known,
        ),
    )

    scheduler.note_thread_known.assert_awaited_once()
    thread_store.set_session_resume.assert_awaited_once_with(
        msg.room_id,
        msg.thread_root_event_id,
        token,
    )
    chat_store.set_session_resume.assert_not_called()
