"""Tests for bridge/cancel.py - cancel handling."""

from __future__ import annotations

import pytest
import anyio

from takopi.api import (
    ExecBridgeConfig,
    MessageRef,
    RenderedMessage,
    RunningTask,
    SendOptions,
)
from takopi_matrix.bridge.cancel import (
    _CANCEL_REACTIONS,
    _is_cancel_command,
    _handle_cancel,
    _handle_cancel_reaction,
)
from takopi_matrix.bridge.config import MatrixBridgeConfig
from takopi_matrix.types import MatrixIncomingMessage, MatrixReaction
from matrix_fixtures import MATRIX_ROOM_ID, MATRIX_EVENT_ID, MATRIX_SENDER


# --- _is_cancel_command tests (already in test_matrix_bridge.py, but more thorough) ---


def test_cancel_reactions_set() -> None:
    """Cancel reactions include expected values."""
    assert "❌" in _CANCEL_REACTIONS
    assert "x" in _CANCEL_REACTIONS
    assert "X" in _CANCEL_REACTIONS
    assert len(_CANCEL_REACTIONS) == 3


# --- Fake transport and config ---


class FakeTransport:
    """Fake transport for cancel tests."""

    def __init__(self) -> None:
        self.send_calls: list[dict] = []
        self._next_id = 1

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        ref = MessageRef(channel_id=channel_id, message_id=f"$sent{self._next_id}")
        self._next_id += 1
        self.send_calls.append({
            "channel_id": channel_id,
            "message": message,
            "options": options,
        })
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        return True

    async def close(self) -> None:
        pass


class FakePresenter:
    """Fake presenter for cancel tests."""

    def render_progress(self, state, elapsed_s, label=None):
        return RenderedMessage(text="progress")

    def render_final(self, state, elapsed_s, status, answer):
        return RenderedMessage(text=f"{status}: {answer}")


def _make_config(transport: FakeTransport) -> MatrixBridgeConfig:
    """Create a minimal MatrixBridgeConfig for testing."""
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )
    # We need a minimal config that just has exec_cfg
    # Using object with attribute for simplicity
    class MinimalConfig:
        pass

    cfg = MinimalConfig()
    cfg.exec_cfg = exec_cfg
    return cfg  # type: ignore


# --- _handle_cancel tests ---


@pytest.mark.anyio
async def test_handle_cancel_no_reply_prompts_user() -> None:
    """Cancel without reply prompts user to reply to progress message."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/cancel",
        reply_to_event_id=None,
        reply_to_text=None,
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "reply to the progress message" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_with_reply_text_but_no_id() -> None:
    """Cancel with reply_to_text but no reply_to_event_id says nothing running."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/cancel",
        reply_to_event_id=None,
        reply_to_text="some old message",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "nothing is currently running" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_reply_no_running_task() -> None:
    """Cancel reply to message with no running task says nothing running."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    progress_id = "$progress:example.org"
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/cancel",
        reply_to_event_id=progress_id,
        reply_to_text=None,
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    await _handle_cancel(cfg, msg, running_tasks)

    assert len(transport.send_calls) == 1
    assert "nothing is currently running" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_handle_cancel_cancels_running_task() -> None:
    """Cancel reply to progress message cancels the running task."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    progress_id = "$progress:example.org"
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/cancel",
        reply_to_event_id=progress_id,
        reply_to_text=None,
    )
    running_task = RunningTask()
    running_tasks = {
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id=progress_id): running_task
    }

    await _handle_cancel(cfg, msg, running_tasks)

    # No error message sent
    assert len(transport.send_calls) == 0
    # Task was cancelled
    assert running_task.cancel_requested.is_set()


@pytest.mark.anyio
async def test_handle_cancel_only_cancels_matching_task() -> None:
    """Cancel only affects the task for the replied-to message."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    progress_id_1 = "$progress1:example.org"
    progress_id_2 = "$progress2:example.org"
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/cancel",
        reply_to_event_id=progress_id_1,
        reply_to_text=None,
    )
    task1 = RunningTask()
    task2 = RunningTask()
    running_tasks = {
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id=progress_id_1): task1,
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id=progress_id_2): task2,
    }

    await _handle_cancel(cfg, msg, running_tasks)

    assert task1.cancel_requested.is_set()
    assert not task2.cancel_requested.is_set()


# --- _handle_cancel_reaction tests ---


@pytest.mark.anyio
async def test_handle_cancel_reaction_ignores_non_cancel() -> None:
    """Non-cancel reactions are ignored."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    reaction = MatrixReaction(
        room_id=MATRIX_ROOM_ID,
        event_id="$reaction:example.org",
        target_event_id="$progress:example.org",
        sender=MATRIX_SENDER,
        key="👍",  # Not a cancel reaction
    )
    task = RunningTask()
    running_tasks = {
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$progress:example.org"): task
    }

    await _handle_cancel_reaction(cfg, reaction, running_tasks)

    assert not task.cancel_requested.is_set()


@pytest.mark.anyio
async def test_handle_cancel_reaction_x_emoji() -> None:
    """❌ reaction cancels task."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    reaction = MatrixReaction(
        room_id=MATRIX_ROOM_ID,
        event_id="$reaction:example.org",
        target_event_id="$progress:example.org",
        sender=MATRIX_SENDER,
        key="❌",
    )
    task = RunningTask()
    running_tasks = {
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$progress:example.org"): task
    }

    await _handle_cancel_reaction(cfg, reaction, running_tasks)

    assert task.cancel_requested.is_set()


@pytest.mark.anyio
async def test_handle_cancel_reaction_lowercase_x() -> None:
    """'x' reaction cancels task."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    reaction = MatrixReaction(
        room_id=MATRIX_ROOM_ID,
        event_id="$reaction:example.org",
        target_event_id="$progress:example.org",
        sender=MATRIX_SENDER,
        key="x",
    )
    task = RunningTask()
    running_tasks = {
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$progress:example.org"): task
    }

    await _handle_cancel_reaction(cfg, reaction, running_tasks)

    assert task.cancel_requested.is_set()


@pytest.mark.anyio
async def test_handle_cancel_reaction_uppercase_x() -> None:
    """'X' reaction cancels task."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    reaction = MatrixReaction(
        room_id=MATRIX_ROOM_ID,
        event_id="$reaction:example.org",
        target_event_id="$progress:example.org",
        sender=MATRIX_SENDER,
        key="X",
    )
    task = RunningTask()
    running_tasks = {
        MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$progress:example.org"): task
    }

    await _handle_cancel_reaction(cfg, reaction, running_tasks)

    assert task.cancel_requested.is_set()


@pytest.mark.anyio
async def test_handle_cancel_reaction_no_matching_task() -> None:
    """Cancel reaction on non-progress message is ignored."""
    transport = FakeTransport()
    cfg = _make_config(transport)
    reaction = MatrixReaction(
        room_id=MATRIX_ROOM_ID,
        event_id="$reaction:example.org",
        target_event_id="$random:example.org",  # Not a progress message
        sender=MATRIX_SENDER,
        key="❌",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    # Should not raise
    await _handle_cancel_reaction(cfg, reaction, running_tasks)
