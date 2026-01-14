"""Event processing pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

import anyio

from takopi.api import (
    ConfigError,
    EngineId,
    ExecBridgeConfig,
    IncomingMessage as RunnerIncomingMessage,
    MessageRef,
    RenderedMessage,
    ResumeToken,
    RunContext,
    Runner,
    RunnerUnavailableError,
    RunningTask,
    RunningTasks,
    SendOptions,
    TransportRuntime,
    handle_message,
)
from takopi.logging import bind_run_context, clear_context, get_logger
from takopi.progress import ProgressTracker
from takopi.utils.paths import reset_run_base_dir, set_run_base_dir

from ..client import (
    parse_reaction,
    parse_room_audio,
    parse_room_media,
    parse_room_message,
)
from ..types import MatrixIncomingMessage, MatrixReaction
from .config import MatrixBridgeConfig

logger = get_logger(__name__)


class ExponentialBackoff:
    """Exponential backoff for reconnection."""

    def __init__(
        self,
        initial: float = 1.0,
        maximum: float = 60.0,
        multiplier: float = 2.0,
    ) -> None:
        self.initial = initial
        self.maximum = maximum
        self.multiplier = multiplier
        self.current = initial

    def next(self) -> float:
        delay = self.current
        self.current = min(self.current * self.multiplier, self.maximum)
        return delay

    def reset(self) -> None:
        self.current = self.initial


async def _send_plain(
    exec_cfg: ExecBridgeConfig,
    *,
    room_id: str,
    reply_to_event_id: str,
    text: str,
    notify: bool = True,
) -> None:
    """Send a plain text message as a reply."""
    reply_to = MessageRef(channel_id=room_id, message_id=reply_to_event_id)
    await exec_cfg.transport.send(
        channel_id=room_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=notify),
    )


async def _wait_for_resume(running_task: RunningTask) -> ResumeToken | None:
    """Wait for resume token to become available."""
    if running_task.resume is not None:
        return running_task.resume
    resume: ResumeToken | None = None

    async with anyio.create_task_group() as tg:

        async def wait_resume() -> None:
            nonlocal resume
            await running_task.resume_ready.wait()
            resume = running_task.resume
            tg.cancel_scope.cancel()

        async def wait_done() -> None:
            await running_task.done.wait()
            tg.cancel_scope.cancel()

        tg.start_soon(wait_resume)
        tg.start_soon(wait_done)

    return resume


async def _send_with_resume(
    cfg: MatrixBridgeConfig,
    enqueue: Callable[[str, str, str, ResumeToken, RunContext | None], Awaitable[None]],
    running_task: RunningTask,
    room_id: str,
    event_id: str,
    text: str,
) -> None:
    """Send a message with resume support."""
    resume = await _wait_for_resume(running_task)
    if resume is None:
        await _send_plain(
            cfg.exec_cfg,
            room_id=room_id,
            reply_to_event_id=event_id,
            text="resume token not ready yet; try replying to the final message.",
            notify=False,
        )
        return
    await enqueue(room_id, event_id, text, resume, running_task.context)


async def _send_runner_unavailable(
    exec_cfg: ExecBridgeConfig,
    *,
    room_id: str,
    event_id: str,
    resume_token: ResumeToken | None,
    runner: Runner,
    reason: str,
) -> None:
    """Send error message when runner is unavailable."""
    tracker = ProgressTracker(engine=runner.engine)
    tracker.set_resume(resume_token)
    state = tracker.snapshot(resume_formatter=runner.format_resume)
    message = exec_cfg.presenter.render_final(
        state,
        elapsed_s=0.0,
        status="error",
        answer=f"error:\n{reason}",
    )
    reply_to = MessageRef(channel_id=room_id, message_id=event_id)
    await exec_cfg.transport.send(
        channel_id=room_id,
        message=message,
        options=SendOptions(reply_to=reply_to, notify=True),
    )


async def _run_engine(
    *,
    exec_cfg: ExecBridgeConfig,
    runtime: TransportRuntime,
    running_tasks: RunningTasks | None,
    room_id: str,
    event_id: str,
    text: str,
    resume_token: ResumeToken | None,
    context: RunContext | None,
    reply_ref: MessageRef | None = None,
    on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
    | None = None,
    engine_override: EngineId | None = None,
) -> None:
    """Run the engine to handle a message."""
    try:
        try:
            entry = runtime.resolve_runner(
                resume_token=resume_token,
                engine_override=engine_override,
            )
        except RunnerUnavailableError as exc:
            await _send_plain(
                exec_cfg,
                room_id=room_id,
                reply_to_event_id=event_id,
                text=f"error:\n{exc}",
            )
            return
        if not entry.available:
            reason = entry.issue or "engine unavailable"
            await _send_runner_unavailable(
                exec_cfg,
                room_id=room_id,
                event_id=event_id,
                resume_token=resume_token,
                runner=entry.runner,
                reason=reason,
            )
            return
        try:
            cwd = runtime.resolve_run_cwd(context)
        except ConfigError as exc:
            await _send_plain(
                exec_cfg,
                room_id=room_id,
                reply_to_event_id=event_id,
                text=f"error:\n{exc}",
            )
            return
        run_base_token = set_run_base_dir(cwd)
        try:
            run_fields = {
                "room_id": room_id,
                "event_id": event_id,
                "engine": entry.runner.engine,
                "resume": resume_token.value if resume_token else None,
            }
            if context is not None:
                run_fields["project"] = context.project
                run_fields["branch"] = context.branch
            if cwd is not None:
                run_fields["cwd"] = str(cwd)
            bind_run_context(**run_fields)
            context_line = runtime.format_context_line(context)
            incoming = RunnerIncomingMessage(
                channel_id=room_id,
                message_id=event_id,
                text=text,
                reply_to=reply_ref,
            )
            await handle_message(
                exec_cfg,
                runner=entry.runner,
                incoming=incoming,
                resume_token=resume_token,
                context=context,
                context_line=context_line,
                strip_resume_line=runtime.is_resume_line,
                running_tasks=running_tasks,
                on_thread_known=on_thread_known,
            )
        finally:
            reset_run_base_dir(run_base_token)
    except Exception as exc:
        logger.exception(
            "handle.worker_failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
    finally:
        clear_context()


async def _enrich_with_reply_text(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
) -> MatrixIncomingMessage:
    """Fetch the text of the replied-to message if present.

    This is needed to extract resume tokens from replies, since Matrix
    only provides the event ID in reply metadata, not the full text.
    """
    if msg.reply_to_event_id is None:
        return msg

    logger.debug(
        "matrix.enrich_reply.fetching",
        room_id=msg.room_id,
        reply_to_event_id=msg.reply_to_event_id,
    )

    reply_text = await cfg.client.get_event_text(msg.room_id, msg.reply_to_event_id)
    if reply_text is None:
        logger.warning(
            "matrix.enrich_reply.failed",
            room_id=msg.room_id,
            reply_to_event_id=msg.reply_to_event_id,
        )
        return msg

    logger.info(
        "matrix.enrich_reply.success",
        room_id=msg.room_id,
        reply_to_event_id=msg.reply_to_event_id,
        reply_text_length=len(reply_text),
        reply_text_preview=reply_text[:200] if len(reply_text) > 200 else reply_text,
    )

    # Create new message with reply_to_text populated
    return replace(msg, reply_to_text=reply_text)


async def _process_single_event(
    cfg: MatrixBridgeConfig,
    event: object,
    room_id: str,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
    message_queue: anyio.abc.ObjectSendStream[MatrixIncomingMessage],  # type: ignore[name-defined]
    reaction_queue: anyio.abc.ObjectSendStream[MatrixReaction],  # type: ignore[name-defined]
) -> None:
    """Process a single Matrix event (decrypt, parse, enqueue)."""
    event_type = type(event).__name__
    sender = getattr(event, "sender", None)

    # Handle Megolm-encrypted events (need decryption)
    if event_type in ("RoomEncrypted", "MegolmEvent"):
        if sender == own_user_id:
            return

        if cfg.client.e2ee_available:
            decrypted = await cfg.client.decrypt_event(event)
            if decrypted is not None:
                # Use the decrypted event instead
                event = decrypted
                event_type = type(event).__name__
                logger.debug(
                    "matrix.sync.decrypted",
                    room_id=room_id,
                    sender=sender,
                    decrypted_type=event_type,
                )
            else:
                logger.warning(
                    "matrix.sync.decryption_failed",
                    room_id=room_id,
                    sender=sender,
                    hint="Missing session keys - verify devices or wait for key sharing",
                )
                return
        else:
            logger.warning(
                "matrix.sync.e2ee_not_available",
                room_id=room_id,
                sender=sender,
                hint="Install matrix-nio[e2e] for encrypted room support",
            )
            return

    if event_type == "RoomMessageText":
        if room_id not in allowed_room_ids:
            logger.debug(
                "matrix.sync.room_not_allowed",
                room_id=room_id,
                allowed=list(allowed_room_ids),
            )
        elif sender == own_user_id:
            logger.debug("matrix.sync.own_message", room_id=room_id)
        elif cfg.user_allowlist is not None and sender not in cfg.user_allowlist:
            logger.debug(
                "matrix.sync.sender_not_allowed",
                room_id=room_id,
                sender=sender,
                allowlist=list(cfg.user_allowlist),
            )
        else:
            logger.debug(
                "matrix.sync.message_received",
                room_id=room_id,
                sender=sender,
                event_type=event_type,
            )
            msg = parse_room_message(
                event,
                room_id,
                allowed_room_ids=allowed_room_ids,
                own_user_id=own_user_id,
            )
            if msg:
                msg = await _enrich_with_reply_text(cfg, msg)
                await message_queue.send(msg)

    elif event_type in (
        "RoomMessageImage",
        "RoomMessageFile",
        "RoomEncryptedImage",
        "RoomEncryptedFile",
    ):
        if cfg.user_allowlist is not None and sender not in cfg.user_allowlist:
            logger.debug(
                "matrix.sync.sender_not_allowed",
                room_id=room_id,
                sender=sender,
                event_type=event_type,
            )
        else:
            msg = parse_room_media(
                event,
                room_id,
                allowed_room_ids=allowed_room_ids,
                own_user_id=own_user_id,
            )
            if msg:
                msg = await _enrich_with_reply_text(cfg, msg)
                await message_queue.send(msg)

    elif event_type in ("RoomMessageAudio", "RoomEncryptedAudio"):
        if cfg.user_allowlist is not None and sender not in cfg.user_allowlist:
            logger.debug(
                "matrix.sync.sender_not_allowed",
                room_id=room_id,
                sender=sender,
                event_type=event_type,
            )
        else:
            msg = parse_room_audio(
                event,
                room_id,
                allowed_room_ids=allowed_room_ids,
                own_user_id=own_user_id,
            )
            if msg:
                msg = await _enrich_with_reply_text(cfg, msg)
                await message_queue.send(msg)

    elif event_type == "ReactionEvent":
        reaction = parse_reaction(
            event,
            room_id,
            allowed_room_ids=allowed_room_ids,
            own_user_id=own_user_id,
        )
        if reaction:
            await reaction_queue.send(reaction)


async def _process_room_timeline(
    cfg: MatrixBridgeConfig,
    room_id: str,
    room_info: object,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
    message_queue: anyio.abc.ObjectSendStream[MatrixIncomingMessage],  # type: ignore[name-defined]
    reaction_queue: anyio.abc.ObjectSendStream[MatrixReaction],  # type: ignore[name-defined]
) -> None:
    """Process all events in a room's timeline."""
    timeline = getattr(room_info, "timeline", None)
    if timeline is None:
        return

    events = getattr(timeline, "events", [])
    for event in events:
        await _process_single_event(
            cfg,
            event,
            room_id,
            allowed_room_ids=allowed_room_ids,
            own_user_id=own_user_id,
            message_queue=message_queue,
            reaction_queue=reaction_queue,
        )

    # Trust any new devices in rooms we care about
    if cfg.client.e2ee_available and room_id in allowed_room_ids:
        await cfg.client.trust_room_devices(room_id)


async def _process_sync_response(
    cfg: MatrixBridgeConfig,
    response: object,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
    message_queue: anyio.abc.ObjectSendStream[MatrixIncomingMessage],  # type: ignore[name-defined]
    reaction_queue: anyio.abc.ObjectSendStream[MatrixReaction],  # type: ignore[name-defined]
) -> None:
    """Process all rooms in a sync response."""
    rooms = getattr(response, "rooms", None)
    if rooms is None:
        return

    join = getattr(rooms, "join", {})
    for room_id, room_info in join.items():
        await _process_room_timeline(
            cfg,
            room_id,
            room_info,
            allowed_room_ids=allowed_room_ids,
            own_user_id=own_user_id,
            message_queue=message_queue,
            reaction_queue=reaction_queue,
        )
