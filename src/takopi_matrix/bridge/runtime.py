"""Main runtime loop and startup sequence."""

from __future__ import annotations


import anyio

from takopi.api import (
    DirectiveError,
    MessageRef,
    RenderedMessage,
    RunningTasks,
)
from takopi.api import list_command_ids, RESERVED_COMMAND_IDS, get_logger
from takopi.api import ThreadJob, ThreadScheduler

from ..markdown import MarkdownParts

from ..client import MatrixRetryAfter
from ..engine_defaults import (
    build_allowed_room_ids,
    resolve_context_for_room,
    resolve_engine_for_message,
)
from ..trigger_mode import resolve_trigger_mode, should_trigger_run
from ..render import prepare_matrix
from ..types import MatrixIncomingMessage, MatrixReaction
from .cancel import _handle_cancel, _handle_cancel_reaction, _is_cancel_command
from .commands import dispatch_command, parse_slash_command
from .config import MatrixBridgeConfig
from .events import (
    ExponentialBackoff,
    _process_invite_events,
    _process_sync_response,
    _run_engine,
    _send_plain,
    _send_with_resume,
)
from .transcription import _process_file_attachments, _transcribe_voice

logger = get_logger(__name__)

# Queue buffer sizes for message processing
MESSAGE_QUEUE_SIZE = 100  # Max buffered messages before backpressure
REACTION_QUEUE_SIZE = 100  # Max buffered reactions before backpressure


async def _persist_new_rooms(room_ids: list[str], config_path: object) -> None:
    """Persist newly joined room IDs to the config file.

    Uses tomlkit to preserve formatting.
    """
    from pathlib import Path

    if config_path is None or not room_ids:
        return

    # Ensure we have a Path
    if not isinstance(config_path, Path):
        return

    try:
        import tomlkit
    except ImportError:
        logger.warning("matrix.config.tomlkit_not_available")
        return

    try:
        # Read current config
        config_text = config_path.read_text()
        config = tomlkit.parse(config_text)

        # Get current room_ids
        transports = config.get("transports", {})
        matrix = transports.get("matrix", {})
        current_rooms = list(matrix.get("room_ids", []))

        # Add new rooms (avoid duplicates)
        added = []
        for room_id in room_ids:
            if room_id not in current_rooms:
                current_rooms.append(room_id)
                added.append(room_id)

        if not added:
            return

        # Update config (type ignores: tomlkit's Container typing is incomplete)
        if "transports" not in config:
            config["transports"] = tomlkit.table()
        if "matrix" not in config["transports"]:  # type: ignore[operator]
            config["transports"]["matrix"] = tomlkit.table()  # type: ignore[index]

        config["transports"]["matrix"]["room_ids"] = current_rooms  # type: ignore[index]

        # Write back atomically
        temp_path = config_path.with_suffix(".toml.tmp")
        temp_path.write_text(tomlkit.dumps(config))
        temp_path.rename(config_path)

        logger.info(
            "matrix.config.rooms_updated",
            added_rooms=added,
            total_rooms=len(current_rooms),
        )

    except Exception as exc:
        logger.error(
            "matrix.config.update_failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )


async def _send_startup(cfg: MatrixBridgeConfig) -> None:
    """Send startup message to all configured rooms."""
    if not cfg.send_startup_message:
        logger.debug("startup.message.disabled")
        return

    logger.debug("startup.message", text=cfg.startup_msg)
    parts = MarkdownParts(header=cfg.startup_msg)
    text, formatted_body = prepare_matrix(parts)
    message = RenderedMessage(text=text, extra={"formatted_body": formatted_body})

    for room_id in cfg.room_ids:
        sent = await cfg.exec_cfg.transport.send(
            channel_id=room_id,
            message=message,
        )
        if sent is not None:
            logger.info("startup.sent", room_id=room_id)


async def _sync_loop(
    cfg: MatrixBridgeConfig,
    message_queue: anyio.abc.ObjectSendStream[MatrixIncomingMessage],  # type: ignore[name-defined]
    reaction_queue: anyio.abc.ObjectSendStream[MatrixReaction],  # type: ignore[name-defined]
) -> None:
    """Continuous sync loop with reconnection."""
    backoff = ExponentialBackoff()
    allowed_room_ids = build_allowed_room_ids(
        cfg.room_ids, cfg.runtime, cfg.room_project_map
    )
    own_user_id = cfg.client.user_id

    logger.debug(
        "matrix.sync.start",
        allowed_room_ids=list(allowed_room_ids),
        own_user_id=own_user_id,
    )

    while True:
        try:
            response = await cfg.client.sync(timeout_ms=30000)
            if response is None:
                await anyio.sleep(backoff.next())
                continue

            backoff.reset()

            # Process invites first (may add to allowed_room_ids)
            new_rooms = await _process_invite_events(
                cfg,
                response,
                allowed_room_ids=allowed_room_ids,
            )
            if new_rooms:
                await _persist_new_rooms(new_rooms, cfg.config_path)

            await _process_sync_response(
                cfg,
                response,
                allowed_room_ids=allowed_room_ids,
                own_user_id=own_user_id,
                message_queue=message_queue,
                reaction_queue=reaction_queue,
            )

        except MatrixRetryAfter as exc:
            logger.warning("matrix.sync.rate_limited", retry_after=exc.retry_after)
            await anyio.sleep(exc.retry_after)
        except Exception as exc:
            logger.error(
                "matrix.sync.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            await anyio.sleep(backoff.next())


async def _initialize_e2ee_if_available(cfg: MatrixBridgeConfig) -> None:
    """Initialize E2EE crypto store if available."""
    if not cfg.client.e2ee_available:
        return

    if await cfg.client.init_e2ee():
        logger.info("matrix.startup.e2ee_initialized")
    else:
        logger.warning("matrix.startup.e2ee_init_failed")


async def _trust_room_devices_if_e2ee(cfg: MatrixBridgeConfig) -> None:
    """Trust devices and establish encryption sessions in all configured rooms."""
    if not cfg.client.e2ee_available:
        return

    for room_id in cfg.room_ids:
        await cfg.client.trust_room_devices(room_id)
        await cfg.client.ensure_room_keys(room_id)


async def _startup_sequence(cfg: MatrixBridgeConfig) -> bool:
    """Execute startup sequence: login, E2EE init, sync, send startup message.

    Returns:
        True if startup succeeded, False if login failed.
    """
    if not await cfg.client.login():
        logger.error("matrix.startup.login_failed")
        return False

    # Initialize E2EE after login (loads crypto store)
    await _initialize_e2ee_if_available(cfg)

    # Initial sync to populate room list before sending messages
    logger.debug("matrix.startup.initial_sync")
    # If we have a persisted sync token, a normal incremental sync does not
    # include full room state. On a fresh process, that means `client.rooms`
    # and membership/device targeting can be incomplete, which makes E2EE key
    # sharing flaky after restarts. Use full_state once on startup when E2EE is
    # available to rebuild state deterministically.
    await cfg.client.sync(timeout_ms=10000, full_state=cfg.client.e2ee_available)

    # Trust devices and establish encryption sessions (after sync so rooms are known)
    await _trust_room_devices_if_e2ee(cfg)

    for room_id in cfg.room_ids:
        await cfg.client.send_typing(room_id, typing=True)

    await _send_startup(cfg)

    for room_id in cfg.room_ids:
        await cfg.client.send_typing(room_id, typing=False)

    return True


async def run_main_loop(
    cfg: MatrixBridgeConfig,
    *,
    default_engine_override: str | None = None,
) -> None:
    """Main event loop for Matrix transport."""
    _ = default_engine_override  # TODO: Implement engine override support
    running_tasks: RunningTasks = {}
    own_user_id = cfg.client.user_id

    try:
        if not await _startup_sequence(cfg):
            return

        # Fetch display name once at startup (cached for mention detection)
        own_display_name = await cfg.client.get_display_name()
        if own_display_name:
            logger.debug("matrix.display_name.resolved", display_name=own_display_name)
        else:
            logger.warning("matrix.display_name.not_available")

        allowlist = cfg.runtime.allowlist
        command_ids = {
            command_id.lower() for command_id in list_command_ids(allowlist=allowlist)
        }
        reserved_commands = {
            *{engine.lower() for engine in cfg.runtime.engine_ids},
            *{alias.lower() for alias in cfg.runtime.project_aliases()},
            *RESERVED_COMMAND_IDS,
        }

        message_send, message_recv = anyio.create_memory_object_stream[
            MatrixIncomingMessage
        ](max_buffer_size=MESSAGE_QUEUE_SIZE)
        reaction_send, reaction_recv = anyio.create_memory_object_stream[
            MatrixReaction
        ](max_buffer_size=REACTION_QUEUE_SIZE)

        async with anyio.create_task_group() as tg:

            async def run_job(
                room_id: str,
                event_id: str,
                text: str,
                resume_token,
                context,
                reply_ref: MessageRef | None = None,
                on_thread_known=None,
                engine_override=None,
            ) -> None:
                await cfg.client.send_typing(room_id, typing=True)
                try:
                    await _run_engine(
                        exec_cfg=cfg.exec_cfg,
                        runtime=cfg.runtime,
                        running_tasks=running_tasks,
                        room_id=room_id,
                        event_id=event_id,
                        text=text,
                        resume_token=resume_token,
                        context=context,
                        reply_ref=reply_ref,
                        on_thread_known=on_thread_known,
                        engine_override=engine_override,
                    )
                finally:
                    await cfg.client.send_typing(room_id, typing=False)

            async def run_thread_job(job: ThreadJob) -> None:
                await run_job(
                    str(job.chat_id),
                    str(job.user_msg_id),
                    job.text,
                    job.resume_token,
                    job.context,
                    None,
                )

            scheduler = ThreadScheduler(task_group=tg, run_job=run_thread_job)

            tg.start_soon(_sync_loop, cfg, message_send, reaction_send)

            async def process_reactions() -> None:
                async for reaction in reaction_recv:
                    tg.start_soon(_handle_cancel_reaction, cfg, reaction, running_tasks)

            tg.start_soon(process_reactions)

            async for msg in message_recv:
                text = msg.text

                if msg.voice is not None:
                    text = await _transcribe_voice(cfg, msg)
                    if text is None:
                        continue

                if msg.attachments:
                    text = await _process_file_attachments(cfg, msg)

                room_id = msg.room_id
                event_id = msg.event_id
                reply_to = msg.reply_to_event_id
                reply_ref = (
                    MessageRef(channel_id=room_id, message_id=reply_to)
                    if reply_to is not None
                    else None
                )

                await cfg.client.send_read_receipt(room_id, event_id)

                # Check trigger mode for this room
                trigger_mode = await resolve_trigger_mode(
                    room_id=room_id,
                    room_prefs=cfg.room_prefs,
                )
                if trigger_mode == "mentions":
                    # Determine if message is a reply to a bot message
                    reply_to_is_bot = False
                    if reply_to is not None:
                        reply_to_is_bot = (
                            MessageRef(channel_id=room_id, message_id=reply_to)
                            in running_tasks
                        )
                    if not should_trigger_run(
                        text,
                        own_user_id=own_user_id,
                        own_display_name=own_display_name,
                        reply_to_is_bot=reply_to_is_bot,
                        runtime=cfg.runtime,
                        command_ids=command_ids,
                        reserved_room_commands=reserved_commands,
                    ):
                        logger.debug(
                            "matrix.trigger.skipped",
                            room_id=room_id,
                            trigger_mode=trigger_mode,
                        )
                        continue

                if _is_cancel_command(text):
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                command_id, args_text = parse_slash_command(text)
                if command_id is not None and command_id not in reserved_commands:
                    if command_id not in command_ids:
                        command_ids.update(
                            cid.lower() for cid in list_command_ids(allowlist=allowlist)
                        )
                    if command_id in command_ids:
                        tg.start_soon(
                            dispatch_command,
                            cfg,
                            msg,
                            text,
                            command_id,
                            args_text,
                            running_tasks,
                            scheduler,
                            _run_engine,
                        )
                        continue

                reply_text = msg.reply_to_text
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=text,
                        reply_text=reply_text,
                    )
                except DirectiveError as exc:
                    await _send_plain(
                        cfg.exec_cfg,
                        room_id=room_id,
                        reply_to_event_id=event_id,
                        text=f"error:\n{exc}",
                    )
                    continue

                text = resolved.prompt
                resume_token = resolved.resume_token

                # Resolve context: directive takes priority, then room's bound project
                context = resolve_context_for_room(
                    room_id=room_id,
                    directive_context=resolved.context,
                    room_project_map=cfg.room_project_map,
                )

                # Resolve engine using hierarchy:
                # 1. Directive (@engine), 2. Room default, 3. Project default, 4. Global
                engine_resolution = await resolve_engine_for_message(
                    runtime=cfg.runtime,
                    context=context,
                    explicit_engine=resolved.engine_override,
                    room_id=room_id,
                    room_prefs=cfg.room_prefs,
                    room_project_map=cfg.room_project_map,
                )
                engine_override = engine_resolution.engine
                logger.debug(
                    "matrix.engine.resolved",
                    room_id=room_id,
                    engine=engine_override,
                    source=engine_resolution.source,
                    context_project=context.project if context else None,
                )

                if resume_token is None and reply_to is not None:
                    running_task = running_tasks.get(
                        MessageRef(channel_id=room_id, message_id=reply_to)
                    )
                    if running_task is not None:
                        tg.start_soon(
                            _send_with_resume,
                            cfg,
                            scheduler.enqueue_resume,
                            running_task,
                            room_id,
                            event_id,
                            text,
                        )
                        continue

                if resume_token is None:
                    tg.start_soon(
                        run_job,
                        room_id,
                        event_id,
                        text,
                        None,
                        context,
                        reply_ref,
                        scheduler.note_thread_known,
                        engine_override,
                    )
                else:
                    # TODO: Scheduler expects int IDs (Telegram), but Matrix uses str
                    # This requires architectural fix - see plan for generic scheduler design
                    await scheduler.enqueue_resume(
                        room_id,  # str, but expects int
                        event_id,  # str, but expects int
                        text,
                        resume_token,
                        context,
                    )

    finally:
        await cfg.exec_cfg.transport.close()
