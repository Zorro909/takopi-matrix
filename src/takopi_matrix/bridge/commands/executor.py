"""Command executor for Matrix transport."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import anyio

from takopi.api import (
    CommandExecutor,
    ExecBridgeConfig,
    MessageRef,
    RenderedMessage,
    ResumeToken,
    RunMode,
    RunningTasks,
    RunRequest,
    RunResult,
    SendOptions,
    TransportRuntime,
    ThreadScheduler,
)


class _CaptureTransport:
    """A transport that captures messages instead of sending them."""

    def __init__(self) -> None:
        self._next_id = 1
        self.last_message: RenderedMessage | None = None

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        _ = options
        ref = MessageRef(channel_id=channel_id, message_id=str(self._next_id))
        self._next_id += 1
        self.last_message = message
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        _ = ref, wait
        self.last_message = message
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        _ = ref
        return True

    async def close(self) -> None:
        return None


class MatrixCommandExecutor(CommandExecutor):
    """Command executor for Matrix transport."""

    def __init__(
        self,
        *,
        exec_cfg: ExecBridgeConfig,
        runtime: TransportRuntime,
        running_tasks: RunningTasks,
        scheduler: ThreadScheduler,
        room_id: str,
        event_id: str,
        run_engine_fn: Callable[..., Awaitable[None]],
        on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
        | None = None,
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._room_id = room_id
        self._event_id = event_id
        self._reply_ref = MessageRef(channel_id=room_id, message_id=event_id)
        self._run_engine_fn = run_engine_fn
        self._on_thread_known = on_thread_known

    async def send(
        self,
        message: RenderedMessage | str,
        *,
        reply_to: MessageRef | None = None,
        notify: bool = True,
    ) -> MessageRef | None:
        rendered = (
            message
            if isinstance(message, RenderedMessage)
            else RenderedMessage(text=message)
        )
        reply_ref = self._reply_ref if reply_to is None else reply_to
        return await self._exec_cfg.transport.send(
            channel_id=self._room_id,
            message=rendered,
            options=SendOptions(reply_to=reply_ref, notify=notify),
        )

    async def run_one(
        self, request: RunRequest, *, mode: RunMode = "emit"
    ) -> RunResult:
        engine = self._runtime.resolve_engine(
            engine_override=request.engine,
            context=request.context,
        )
        on_thread_known = (
            self._scheduler.note_thread_known
            if self._on_thread_known is None
            else self._on_thread_known
        )
        if mode == "capture":
            capture = _CaptureTransport()
            exec_cfg = ExecBridgeConfig(
                transport=capture,
                presenter=self._exec_cfg.presenter,
                final_notify=False,
            )
            await self._run_engine_fn(
                exec_cfg=exec_cfg,
                runtime=self._runtime,
                running_tasks={},
                room_id=self._room_id,
                event_id=self._event_id,
                text=request.prompt,
                resume_token=None,
                context=request.context,
                reply_ref=self._reply_ref,
                on_thread_known=on_thread_known,
                engine_override=engine,
            )
            return RunResult(engine=engine, message=capture.last_message)
        await self._run_engine_fn(
            exec_cfg=self._exec_cfg,
            runtime=self._runtime,
            running_tasks=self._running_tasks,
            room_id=self._room_id,
            event_id=self._event_id,
            text=request.prompt,
            resume_token=None,
            context=request.context,
            reply_ref=self._reply_ref,
            on_thread_known=on_thread_known,
            engine_override=engine,
        )
        return RunResult(engine=engine, message=None)

    async def run_many(
        self,
        requests: Sequence[RunRequest],
        *,
        mode: RunMode = "emit",
        parallel: bool = False,
    ) -> list[RunResult]:
        if not parallel:
            return [await self.run_one(request, mode=mode) for request in requests]
        results: list[RunResult | None] = [None] * len(requests)

        async with anyio.create_task_group() as tg:

            async def run_idx(idx: int, request: RunRequest) -> None:
                results[idx] = await self.run_one(request, mode=mode)

            for idx, request in enumerate(requests):
                tg.start_soon(run_idx, idx, request)

        return [result for result in results if result is not None]
