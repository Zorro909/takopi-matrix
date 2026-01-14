"""Command parsing and execution."""

from __future__ import annotations

import shlex
from collections.abc import Awaitable, Callable, Sequence

import anyio

from takopi.api import (
    CommandContext,
    CommandExecutor,
    ConfigError,
    ExecBridgeConfig,
    MessageRef,
    RenderedMessage,
    RunMode,
    RunningTasks,
    RunRequest,
    RunResult,
    SendOptions,
    TransportRuntime,
)
from takopi.commands import get_command
from takopi.logging import get_logger
from takopi.scheduler import ThreadScheduler

from ..types import MatrixIncomingMessage
from .config import MatrixBridgeConfig

logger = get_logger(__name__)


def _parse_slash_command(text: str) -> tuple[str | None, str]:
    """Parse a slash command from text, returning (command_id, args_text)."""
    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return None, text
    lines = stripped.splitlines()
    if not lines:
        return None, text
    first_line = lines[0]
    token, _, rest = first_line.partition(" ")
    command = token[1:]
    if not command:
        return None, text
    if "@" in command:
        command = command.split("@", 1)[0]
    args_text = rest
    if len(lines) > 1:
        tail = "\n".join(lines[1:])
        args_text = f"{args_text}\n{tail}" if args_text else tail
    return command.lower(), args_text


def _split_command_args(text: str) -> tuple[str, ...]:
    """Split command arguments using shell-like parsing."""
    if not text.strip():
        return ()
    try:
        return tuple(shlex.split(text))
    except ValueError:
        return tuple(text.split())


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


class _MatrixCommandExecutor(CommandExecutor):
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
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._room_id = room_id
        self._event_id = event_id
        self._reply_ref = MessageRef(channel_id=room_id, message_id=event_id)
        self._run_engine_fn = run_engine_fn

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
                on_thread_known=None,
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
            on_thread_known=self._scheduler.note_thread_known,
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


async def _dispatch_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    text: str,
    command_id: str,
    args_text: str,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler,
    run_engine_fn: Callable[..., Awaitable[None]],
) -> None:
    """Dispatch and execute a slash command."""
    allowlist = cfg.runtime.allowlist
    room_id = msg.room_id
    event_id = msg.event_id
    reply_ref = (
        MessageRef(channel_id=room_id, message_id=msg.reply_to_event_id)
        if msg.reply_to_event_id is not None
        else None
    )
    executor = _MatrixCommandExecutor(
        exec_cfg=cfg.exec_cfg,
        runtime=cfg.runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=room_id,
        event_id=event_id,
        run_engine_fn=run_engine_fn,
    )
    message_ref = MessageRef(channel_id=room_id, message_id=event_id)
    try:
        backend = get_command(command_id, allowlist=allowlist, required=False)
    except ConfigError as exc:
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    if backend is None:
        return
    try:
        plugin_config = cfg.runtime.plugin_config(command_id)
    except ConfigError as exc:
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    ctx = CommandContext(
        command=command_id,
        text=text,
        args_text=args_text,
        args=_split_command_args(args_text),
        message=message_ref,
        reply_to=reply_ref,
        reply_text=msg.reply_to_text,
        config_path=cfg.runtime.config_path,
        plugin_config=plugin_config,
        runtime=cfg.runtime,
        executor=executor,
    )
    try:
        result = await backend.handle(ctx)
    except Exception as exc:
        logger.exception(
            "command.failed",
            command=command_id,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        await executor.send(f"error:\n{exc}", reply_to=message_ref, notify=True)
        return
    if result is not None:
        reply_to = message_ref if result.reply_to is None else result.reply_to
        await executor.send(result.text, reply_to=reply_to, notify=result.notify)
    return None
