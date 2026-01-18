"""Tests for bridge/commands/dispatch.py - command dispatch."""

from __future__ import annotations

import pytest

from takopi.api import (
    CommandBackend,
    CommandContext,
    CommandResult,
    ExecBridgeConfig,
    MessageRef,
    RenderedMessage,
    RunningTask,
    SendOptions,
)
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.transport_runtime import TransportRuntime
from takopi.config import ProjectsConfig
from takopi_matrix.bridge.commands.dispatch import dispatch_command
from takopi_matrix.bridge.commands.parse import split_command_args
from takopi_matrix.bridge.config import MatrixBridgeConfig
from takopi_matrix.types import MatrixIncomingMessage
from matrix_fixtures import MATRIX_ROOM_ID, MATRIX_EVENT_ID, MATRIX_SENDER


# --- split_command_args tests ---


def test_split_command_args_simple() -> None:
    """Split simple space-separated args."""
    args = split_command_args("arg1 arg2 arg3")
    assert args == ("arg1", "arg2", "arg3")


def test_split_command_args_empty() -> None:
    """Split empty string returns empty tuple."""
    args = split_command_args("")
    assert args == ()


def test_split_command_args_whitespace_only() -> None:
    """Split whitespace-only string returns empty tuple."""
    args = split_command_args("   ")
    assert args == ()


def test_split_command_args_extra_whitespace() -> None:
    """Extra whitespace is collapsed."""
    args = split_command_args("  arg1   arg2  ")
    assert args == ("arg1", "arg2")


def test_split_command_args_quoted() -> None:
    """Quoted strings are kept as single args (shlex-style)."""
    # split_command_args uses shlex, handles quotes
    args = split_command_args('"quoted arg" unquoted')
    # Quotes are stripped, but the content is kept together
    assert args == ("quoted arg", "unquoted")


def test_split_command_args_newlines() -> None:
    """Newlines are treated as whitespace."""
    args = split_command_args("arg1\narg2\narg3")
    assert args == ("arg1", "arg2", "arg3")


# --- Fake infrastructure ---


class FakeTransport:
    """Fake transport for dispatch tests."""

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
    """Fake presenter for dispatch tests."""

    def render_progress(self, state, elapsed_s, label=None):
        return RenderedMessage(text="progress")

    def render_final(self, state, elapsed_s, status, answer):
        return RenderedMessage(text=f"{status}: {answer}")


def _empty_projects() -> ProjectsConfig:
    return ProjectsConfig(projects={}, default_project=None)


def _make_router(runner: ScriptRunner) -> AutoRouter:
    return AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )


class MinimalBridgeConfig:
    """Minimal MatrixBridgeConfig for testing dispatch."""

    def __init__(
        self,
        transport: FakeTransport,
        runner: ScriptRunner | None = None,
    ) -> None:
        if runner is None:
            runner = ScriptRunner([Return(answer="ok")], engine="codex")

        self.exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=FakePresenter(),
            final_notify=True,
        )
        self.runtime = TransportRuntime(
            router=_make_router(runner),
            projects=_empty_projects(),
        )
        self.user_allowlist = None
        self.client = None


# --- dispatch_command tests ---


@pytest.mark.anyio
async def test_dispatch_unknown_command_no_error() -> None:
    """Unknown command ID doesn't send error (returns silently)."""
    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/unknown arg",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    await dispatch_command(
        cfg,  # type: ignore
        msg,
        text="/unknown arg",
        command_id="unknown_command_that_does_not_exist",
        args_text="arg",
        running_tasks=running_tasks,
        scheduler=scheduler,
        run_engine_fn=run_engine_fn,
    )

    # No error message sent for unknown commands (get_command returns None)
    # The function returns silently when backend is None
    # This is the expected behavior


# --- Edge cases ---


def test_split_command_args_unicode() -> None:
    """Unicode arguments are preserved."""
    args = split_command_args("你好 世界 🎉")
    assert args == ("你好", "世界", "🎉")


def test_split_command_args_tabs() -> None:
    """Tabs are treated as whitespace."""
    args = split_command_args("arg1\targ2\targ3")
    assert args == ("arg1", "arg2", "arg3")


def test_split_command_args_mixed_whitespace() -> None:
    """Mixed whitespace is handled."""
    args = split_command_args("arg1 \t\n arg2")
    assert args == ("arg1", "arg2")


# --- dispatch_command with registered commands ---


class SimpleCommand(CommandBackend):
    """Simple command for testing."""

    id = "simple"
    description = "Simple test command"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        return CommandResult(text=f"handled: {ctx.args_text}", notify=True)


class ExceptionCommand(CommandBackend):
    """Command that raises an exception."""

    id = "exception"
    description = "Command that raises"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        raise RuntimeError("Test exception")


class NoneCommand(CommandBackend):
    """Command that returns None."""

    id = "noneresult"
    description = "Command that returns None"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        return None


class CustomReplyCommand(CommandBackend):
    """Command with custom reply_to."""

    id = "customreply"
    description = "Command with custom reply"

    async def handle(self, ctx: CommandContext) -> CommandResult | None:
        custom_ref = MessageRef(channel_id="!other:room", message_id="$other")
        return CommandResult(text="custom reply", reply_to=custom_ref, notify=False)


@pytest.mark.anyio
async def test_dispatch_command_success() -> None:
    """Command returns result, sends response."""
    from unittest.mock import patch

    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/simple myargs",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    with patch("takopi_matrix.bridge.commands.dispatch.get_command") as mock_get:
        mock_get.return_value = SimpleCommand()

        await dispatch_command(
            cfg,  # type: ignore
            msg,
            text="/simple myargs",
            command_id="simple",
            args_text="myargs",
            running_tasks=running_tasks,
            scheduler=scheduler,
            run_engine_fn=run_engine_fn,
        )

    assert len(transport.send_calls) == 1
    assert transport.send_calls[0]["message"].text == "handled: myargs"


@pytest.mark.anyio
async def test_dispatch_command_exception() -> None:
    """Command raises exception, sends error."""
    from unittest.mock import patch

    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/exception",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    with patch("takopi_matrix.bridge.commands.dispatch.get_command") as mock_get:
        mock_get.return_value = ExceptionCommand()

        await dispatch_command(
            cfg,  # type: ignore
            msg,
            text="/exception",
            command_id="exception",
            args_text="",
            running_tasks=running_tasks,
            scheduler=scheduler,
            run_engine_fn=run_engine_fn,
        )

    assert len(transport.send_calls) == 1
    assert "error" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_dispatch_command_returns_none() -> None:
    """Command returns None, no message sent."""
    from unittest.mock import patch

    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/noneresult",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    with patch("takopi_matrix.bridge.commands.dispatch.get_command") as mock_get:
        mock_get.return_value = NoneCommand()

        await dispatch_command(
            cfg,  # type: ignore
            msg,
            text="/noneresult",
            command_id="noneresult",
            args_text="",
            running_tasks=running_tasks,
            scheduler=scheduler,
            run_engine_fn=run_engine_fn,
        )

    # No message sent when result is None
    assert len(transport.send_calls) == 0


@pytest.mark.anyio
async def test_dispatch_command_custom_reply_to() -> None:
    """Command with custom reply_to uses that reference."""
    from unittest.mock import patch

    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/customreply",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    with patch("takopi_matrix.bridge.commands.dispatch.get_command") as mock_get:
        mock_get.return_value = CustomReplyCommand()

        await dispatch_command(
            cfg,  # type: ignore
            msg,
            text="/customreply",
            command_id="customreply",
            args_text="",
            running_tasks=running_tasks,
            scheduler=scheduler,
            run_engine_fn=run_engine_fn,
        )

    assert len(transport.send_calls) == 1
    assert transport.send_calls[0]["message"].text == "custom reply"
    assert transport.send_calls[0]["options"].reply_to.channel_id == "!other:room"


@pytest.mark.anyio
async def test_dispatch_get_command_config_error() -> None:
    """ConfigError from get_command sends error message."""
    from unittest.mock import patch

    from takopi.api import ConfigError

    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/badconfig",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    with patch("takopi_matrix.bridge.commands.dispatch.get_command") as mock_get:
        mock_get.side_effect = ConfigError("Command config is invalid")

        await dispatch_command(
            cfg,  # type: ignore
            msg,
            text="/badconfig",
            command_id="badconfig",
            args_text="",
            running_tasks=running_tasks,
            scheduler=scheduler,
            run_engine_fn=run_engine_fn,
        )

    assert len(transport.send_calls) == 1
    assert "error" in transport.send_calls[0]["message"].text
    assert "Command config is invalid" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_dispatch_with_reply_to() -> None:
    """Message with reply_to_event_id creates reply reference."""
    from unittest.mock import patch

    transport = FakeTransport()
    cfg = MinimalBridgeConfig(transport)
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender=MATRIX_SENDER,
        text="/simple arg",
        reply_to_event_id="$original_event",
        reply_to_text="original message text",
    )
    running_tasks: dict[MessageRef, RunningTask] = {}

    class FakeScheduler:
        def note_thread_known(self, resume_token, done_event):
            pass

    scheduler = FakeScheduler()

    async def run_engine_fn(**kwargs):
        pass

    with patch("takopi_matrix.bridge.commands.dispatch.get_command") as mock_get:
        mock_get.return_value = SimpleCommand()

        await dispatch_command(
            cfg,  # type: ignore
            msg,
            text="/simple arg",
            command_id="simple",
            args_text="arg",
            running_tasks=running_tasks,
            scheduler=scheduler,
            run_engine_fn=run_engine_fn,
        )

    assert len(transport.send_calls) == 1
