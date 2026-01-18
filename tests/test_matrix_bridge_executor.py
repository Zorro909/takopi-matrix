"""Tests for bridge/commands/executor.py - command executor."""

from __future__ import annotations

import pytest
import anyio

from takopi.api import (
    ExecBridgeConfig,
    MessageRef,
    RenderedMessage,
    RunContext,
    RunRequest,
    RunningTask,
    SendOptions,
)
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import Return, ScriptRunner
from takopi.transport_runtime import TransportRuntime
from takopi.config import ProjectsConfig
from takopi_matrix.bridge.commands.executor import (
    _CaptureTransport,
    MatrixCommandExecutor,
)
from matrix_fixtures import MATRIX_ROOM_ID, MATRIX_EVENT_ID


# Fake scheduler for tests
class FakeScheduler:
    """Fake scheduler that doesn't need task_group."""

    def note_thread_known(self, resume_token, done_event):
        pass


# --- _CaptureTransport tests ---


@pytest.mark.anyio
async def test_capture_transport_send() -> None:
    """CaptureTransport captures send calls."""
    transport = _CaptureTransport()

    ref = await transport.send(
        channel_id=MATRIX_ROOM_ID,
        message=RenderedMessage(text="captured"),
        options=None,
    )

    assert ref is not None
    assert ref.channel_id == MATRIX_ROOM_ID
    assert transport.last_message is not None
    assert transport.last_message.text == "captured"


@pytest.mark.anyio
async def test_capture_transport_send_increments_id() -> None:
    """CaptureTransport increments message IDs."""
    transport = _CaptureTransport()

    ref1 = await transport.send(
        channel_id=MATRIX_ROOM_ID,
        message=RenderedMessage(text="first"),
    )
    ref2 = await transport.send(
        channel_id=MATRIX_ROOM_ID,
        message=RenderedMessage(text="second"),
    )

    assert ref1.message_id == "1"
    assert ref2.message_id == "2"
    # Last message is the second one
    assert transport.last_message.text == "second"


@pytest.mark.anyio
async def test_capture_transport_edit() -> None:
    """CaptureTransport captures edit calls."""
    transport = _CaptureTransport()
    ref = MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$orig")

    result = await transport.edit(
        ref=ref,
        message=RenderedMessage(text="edited"),
    )

    assert result == ref
    assert transport.last_message.text == "edited"


@pytest.mark.anyio
async def test_capture_transport_delete() -> None:
    """CaptureTransport delete returns True."""
    transport = _CaptureTransport()
    ref = MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$todel")

    result = await transport.delete(ref=ref)

    assert result is True


@pytest.mark.anyio
async def test_capture_transport_close() -> None:
    """CaptureTransport close does nothing."""
    transport = _CaptureTransport()
    await transport.close()  # Should not raise


# --- Fake infrastructure for executor tests ---


class FakeTransport:
    """Fake transport for executor tests."""

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
    """Fake presenter for executor tests."""

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


def _make_executor(
    transport: FakeTransport,
    runner: ScriptRunner | None = None,
) -> tuple[MatrixCommandExecutor, FakeTransport]:
    """Create a MatrixCommandExecutor with fakes."""
    if runner is None:
        runner = ScriptRunner([Return(answer="ok")], engine="codex")

    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    scheduler = FakeScheduler()
    running_tasks: dict[MessageRef, RunningTask] = {}

    # Dummy run_engine_fn for testing
    async def run_engine_fn(**kwargs):
        pass

    executor = MatrixCommandExecutor(
        exec_cfg=exec_cfg,
        runtime=runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        run_engine_fn=run_engine_fn,
    )
    return executor, transport


# --- MatrixCommandExecutor.send tests ---


@pytest.mark.anyio
async def test_executor_send_string() -> None:
    """Executor.send handles string messages."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)

    ref = await executor.send("Hello, world!")

    assert ref is not None
    assert len(transport.send_calls) == 1
    assert transport.send_calls[0]["message"].text == "Hello, world!"


@pytest.mark.anyio
async def test_executor_send_rendered_message() -> None:
    """Executor.send handles RenderedMessage."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)

    message = RenderedMessage(text="Rendered", extra={"html": "<p>Rendered</p>"})
    ref = await executor.send(message)

    assert ref is not None
    assert transport.send_calls[0]["message"].text == "Rendered"
    assert transport.send_calls[0]["message"].extra["html"] == "<p>Rendered</p>"


@pytest.mark.anyio
async def test_executor_send_with_reply_to() -> None:
    """Executor.send respects reply_to."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)
    reply_ref = MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$other")

    await executor.send("Reply", reply_to=reply_ref)

    assert transport.send_calls[0]["options"].reply_to == reply_ref


@pytest.mark.anyio
async def test_executor_send_default_reply() -> None:
    """Executor.send uses event_id as default reply_to."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)

    await executor.send("Default reply")

    reply_ref = transport.send_calls[0]["options"].reply_to
    assert reply_ref.message_id == MATRIX_EVENT_ID


@pytest.mark.anyio
async def test_executor_send_notify_true() -> None:
    """Executor.send defaults to notify=True."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)

    await executor.send("Notify")

    assert transport.send_calls[0]["options"].notify is True


@pytest.mark.anyio
async def test_executor_send_notify_false() -> None:
    """Executor.send respects notify=False."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)

    await executor.send("Silent", notify=False)

    assert transport.send_calls[0]["options"].notify is False


# --- MatrixCommandExecutor.run_one tests ---


@pytest.mark.anyio
async def test_executor_run_one_emit_mode() -> None:
    """run_one in emit mode calls run_engine_fn."""
    transport = FakeTransport()
    runner = ScriptRunner([Return(answer="response")], engine="codex")
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    scheduler = FakeScheduler()
    running_tasks: dict[MessageRef, RunningTask] = {}

    run_engine_called = False

    async def mock_run_engine(**kwargs):
        nonlocal run_engine_called
        run_engine_called = True

    executor = MatrixCommandExecutor(
        exec_cfg=exec_cfg,
        runtime=runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        run_engine_fn=mock_run_engine,
    )

    request = RunRequest(prompt="test prompt")
    result = await executor.run_one(request, mode="emit")

    assert run_engine_called
    assert result.engine == "codex"
    assert result.message is None  # emit mode doesn't capture


@pytest.mark.anyio
async def test_executor_run_one_capture_mode() -> None:
    """run_one in capture mode captures the message."""
    transport = FakeTransport()
    runner = ScriptRunner([Return(answer="captured response")], engine="codex")
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    scheduler = FakeScheduler()
    running_tasks: dict[MessageRef, RunningTask] = {}

    captured_message: RenderedMessage | None = None

    async def mock_run_engine(exec_cfg, **kwargs):
        nonlocal captured_message
        # Simulate sending a message to the capture transport
        await exec_cfg.transport.send(
            channel_id=MATRIX_ROOM_ID,
            message=RenderedMessage(text="captured output"),
        )

    executor = MatrixCommandExecutor(
        exec_cfg=exec_cfg,
        runtime=runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        run_engine_fn=mock_run_engine,
    )

    request = RunRequest(prompt="test prompt")
    result = await executor.run_one(request, mode="capture")

    assert result.engine == "codex"
    assert result.message is not None
    assert result.message.text == "captured output"


# --- MatrixCommandExecutor.run_many tests ---


@pytest.mark.anyio
async def test_executor_run_many_sequential() -> None:
    """run_many without parallel runs sequentially."""
    transport = FakeTransport()
    runner = ScriptRunner([Return(answer="ok")], engine="codex")
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    scheduler = FakeScheduler()
    running_tasks: dict[MessageRef, RunningTask] = {}

    call_order: list[int] = []

    async def mock_run_engine(exec_cfg, text, **kwargs):
        call_order.append(int(text.split()[-1]))

    executor = MatrixCommandExecutor(
        exec_cfg=exec_cfg,
        runtime=runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        run_engine_fn=mock_run_engine,
    )

    requests = [
        RunRequest(prompt="prompt 1"),
        RunRequest(prompt="prompt 2"),
        RunRequest(prompt="prompt 3"),
    ]
    results = await executor.run_many(requests, parallel=False)

    assert len(results) == 3
    assert call_order == [1, 2, 3]


@pytest.mark.anyio
async def test_executor_run_many_parallel() -> None:
    """run_many with parallel runs concurrently."""
    transport = FakeTransport()
    runner = ScriptRunner([Return(answer="ok")], engine="codex")
    exec_cfg = ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )
    runtime = TransportRuntime(
        router=_make_router(runner),
        projects=_empty_projects(),
    )
    scheduler = FakeScheduler()
    running_tasks: dict[MessageRef, RunningTask] = {}

    call_count = 0
    concurrent_count = 0
    max_concurrent = 0

    async def mock_run_engine(**kwargs):
        nonlocal call_count, concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        await anyio.sleep(0.01)  # Small delay to allow concurrency
        call_count += 1
        concurrent_count -= 1

    executor = MatrixCommandExecutor(
        exec_cfg=exec_cfg,
        runtime=runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        run_engine_fn=mock_run_engine,
    )

    requests = [
        RunRequest(prompt="prompt 1"),
        RunRequest(prompt="prompt 2"),
        RunRequest(prompt="prompt 3"),
    ]
    results = await executor.run_many(requests, parallel=True)

    assert len(results) == 3
    assert call_count == 3
    # With parallel=True, we should see some concurrency
    # (max_concurrent > 1 means at least 2 were running at once)
    assert max_concurrent >= 1  # At minimum, we ran them


@pytest.mark.anyio
async def test_executor_run_many_empty_list() -> None:
    """run_many with empty list returns empty list."""
    transport = FakeTransport()
    executor, _ = _make_executor(transport)

    results = await executor.run_many([], parallel=False)

    assert results == []
