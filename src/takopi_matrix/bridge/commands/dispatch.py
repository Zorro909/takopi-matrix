"""Command dispatch for Matrix transport."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import anyio

from takopi.api import (
    CommandContext,
    ConfigError,
    MessageRef,
    ResumeToken,
    RunningTasks,
    ThreadScheduler,
    get_command,
    get_logger,
)

from ...types import MatrixIncomingMessage
from .executor import MatrixCommandExecutor
from .parse import split_command_args

if TYPE_CHECKING:
    from ..config import MatrixBridgeConfig

logger = get_logger(__name__)


async def dispatch_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    text: str,
    command_id: str,
    args_text: str,
    running_tasks: RunningTasks,
    scheduler: ThreadScheduler,
    run_engine_fn: Callable[..., Awaitable[None]],
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
) -> None:
    """Dispatch and execute a slash command.

    Args:
        cfg: The Matrix bridge configuration.
        msg: The incoming Matrix message.
        text: The full message text.
        command_id: The parsed command ID.
        args_text: The command arguments text.
        running_tasks: Dictionary of running tasks.
        scheduler: Thread scheduler for resumption.
        run_engine_fn: Function to run engine.
    """
    allowlist = cfg.runtime.allowlist
    room_id = msg.room_id
    event_id = msg.event_id
    reply_ref = (
        MessageRef(channel_id=room_id, message_id=msg.reply_to_event_id)
        if msg.reply_to_event_id is not None
        else None
    )
    executor = MatrixCommandExecutor(
        exec_cfg=cfg.exec_cfg,
        runtime=cfg.runtime,
        running_tasks=running_tasks,
        scheduler=scheduler,
        room_id=room_id,
        event_id=event_id,
        run_engine_fn=run_engine_fn,
        on_thread_known=on_thread_known,
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
        args=split_command_args(args_text),
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
