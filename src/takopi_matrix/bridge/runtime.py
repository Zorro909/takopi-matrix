"""Main runtime loop and startup sequence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import anyio

from takopi.api import (
    DirectiveError,
    MessageRef,
    RenderedMessage,
    ResumeToken,
    RunContext,
    RunningTasks,
)
from takopi.api import list_command_ids, RESERVED_COMMAND_IDS, get_logger
from takopi.api import ThreadJob, ThreadScheduler
from takopi.runners.run_options import EngineRunOptions

from ..engine_overrides import merge_overrides
from ..markdown import MarkdownParts

from ..client import MatrixRetryAfter
from ..engine_defaults import (
    build_allowed_room_ids,
    resolve_engine_for_message,
)
from ..trigger_mode import resolve_trigger_mode, should_trigger_run
from ..render import prepare_matrix
from ..types import MatrixIncomingMessage, MatrixReaction
from .cancel import _handle_cancel, _handle_cancel_reaction, _is_cancel_command
from .commands import (
    BUILTIN_COMMAND_IDS,
    dispatch_command,
    handle_builtin_command,
    parse_slash_command,
)
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


@dataclass(frozen=True, slots=True)
class _SessionScope:
    room_id: str
    sender: str
    thread_root_event_id: str | None


def _context_overlay(
    base: RunContext | None, override: RunContext | None
) -> RunContext | None:
    """Merge contexts, keeping base project when override only provides branch."""
    if override is None:
        return base
    if base is None:
        return override
    project = override.project if override.project is not None else base.project
    if override.project is None:
        branch = override.branch if override.branch is not None else base.branch
    else:
        branch = override.branch
    if project is None:
        return None
    return RunContext(project=project, branch=branch)


async def _resolve_ambient_context(
    *,
    cfg: MatrixBridgeConfig,
    room_id: str,
    thread_root_event_id: str | None,
) -> RunContext | None:
    base = (
        cfg.room_project_map.context_for_room(room_id)
        if cfg.room_project_map is not None
        else None
    )
    room_bound = (
        await cfg.room_prefs.get_context(room_id)
        if cfg.room_prefs is not None
        else None
    )
    context = _context_overlay(base, room_bound)
    if thread_root_event_id is not None and cfg.thread_state is not None:
        thread_bound = await cfg.thread_state.get_context(room_id, thread_root_event_id)
        context = _context_overlay(context, thread_bound)
    return context


async def _resolve_engine_run_options(
    *,
    cfg: MatrixBridgeConfig,
    room_id: str,
    thread_root_event_id: str | None,
    engine: str,
) -> EngineRunOptions | None:
    thread_override = None
    if thread_root_event_id is not None and cfg.thread_state is not None:
        thread_override = await cfg.thread_state.get_engine_override(
            room_id, thread_root_event_id, engine
        )
    room_override = None
    if cfg.room_prefs is not None:
        room_override = await cfg.room_prefs.get_engine_override(room_id, engine)
    merged = merge_overrides(thread_override, room_override)
    if merged is None:
        return None
    return EngineRunOptions(model=merged.model, reasoning=merged.reasoning)


async def _lookup_session_resume(
    *,
    cfg: MatrixBridgeConfig,
    scope: _SessionScope,
    engine: str,
) -> ResumeToken | None:
    if cfg.session_mode != "chat":
        return None
    if scope.thread_root_event_id is not None and cfg.thread_state is not None:
        return await cfg.thread_state.get_session_resume(
            scope.room_id, scope.thread_root_event_id, engine
        )
    if cfg.chat_sessions is not None:
        return await cfg.chat_sessions.get_session_resume(
            scope.room_id, scope.sender, engine
        )
    return None


async def _store_session_resume(
    *,
    cfg: MatrixBridgeConfig,
    scope: _SessionScope,
    token: ResumeToken,
) -> None:
    if cfg.session_mode != "chat":
        return
    if scope.thread_root_event_id is not None and cfg.thread_state is not None:
        await cfg.thread_state.set_session_resume(
            scope.room_id, scope.thread_root_event_id, token
        )
        return
    if cfg.chat_sessions is not None:
        await cfg.chat_sessions.set_session_resume(scope.room_id, scope.sender, token)


async def _is_reply_to_bot_message(
    *,
    room_id: str,
    reply_to_event_id: str | None,
    own_user_id: str,
    running_tasks: RunningTasks,
    cfg: MatrixBridgeConfig,
) -> bool:
    """Check whether a reply targets a bot message (running or completed)."""
    if reply_to_event_id is None:
        return False

    reply_ref = MessageRef(channel_id=room_id, message_id=reply_to_event_id)
    if reply_ref in running_tasks:
        return True

    try:
        reply_sender = await cfg.client.get_event_sender(room_id, reply_to_event_id)
    except Exception as exc:
        logger.warning(
            "matrix.reply_lookup.failed",
            room_id=room_id,
            reply_to_event_id=reply_to_event_id,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return False

    if reply_sender is None:
        return False
    return reply_sender == own_user_id


def _wrap_on_thread_known(
    *,
    cfg: MatrixBridgeConfig,
    scope: _SessionScope | None,
    base_cb: Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None,
) -> Callable[[ResumeToken, anyio.Event], Awaitable[None]] | None:
    if base_cb is None and scope is None:
        return None

    async def _wrapped(token: ResumeToken, done: anyio.Event) -> None:
        if base_cb is not None:
            await base_cb(token, done)
        if scope is not None:
            await _store_session_resume(cfg=cfg, scope=scope, token=token)

    return _wrapped


def _should_warn_reply_resume_fallback(
    *, msg: MatrixIncomingMessage, resume_token: ResumeToken | None
) -> bool:
    return (
        resume_token is None
        and msg.reply_to_event_id is not None
        and msg.reply_to_text_fetch_failed
    )


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


async def _sync_chat_sessions_cwd_if_enabled(cfg: MatrixBridgeConfig) -> None:
    if cfg.session_mode != "chat" or cfg.chat_sessions is None:
        return
    cwd = Path.cwd().expanduser().resolve()
    cleared = await cfg.chat_sessions.sync_startup_cwd(cwd)
    if cleared:
        logger.info(
            "matrix.chat_sessions.cleared",
            reason="startup_cwd_changed",
            cwd=str(cwd),
        )


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

        await _sync_chat_sessions_cwd_if_enabled(cfg)

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
            *BUILTIN_COMMAND_IDS,
        }

        message_send, message_recv = anyio.create_memory_object_stream[
            MatrixIncomingMessage
        ](max_buffer_size=MESSAGE_QUEUE_SIZE)
        reaction_send, reaction_recv = anyio.create_memory_object_stream[
            MatrixReaction
        ](max_buffer_size=REACTION_QUEUE_SIZE)

        async with anyio.create_task_group() as tg:
            session_scopes_by_msg: dict[tuple[str, str], _SessionScope] = {}

            async def run_job(
                room_id: str,
                event_id: str,
                text: str,
                resume_token,
                context,
                reply_ref: MessageRef | None = None,
                on_thread_known: Callable[[ResumeToken, anyio.Event], Awaitable[None]]
                | None = None,
                engine_override=None,
                session_scope: _SessionScope | None = None,
                run_options: EngineRunOptions | None = None,
            ) -> None:
                wrapped_on_thread_known = _wrap_on_thread_known(
                    cfg=cfg,
                    scope=session_scope,
                    base_cb=on_thread_known,
                )

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
                        on_thread_known=wrapped_on_thread_known,
                        engine_override=engine_override,
                        run_options=run_options,
                    )
                finally:
                    await cfg.client.send_typing(room_id, typing=False)

            async def run_thread_job(job: ThreadJob) -> None:
                room_id = str(job.chat_id)
                thread_root_event_id = (
                    str(job.thread_id) if job.thread_id is not None else None
                )
                run_options = await _resolve_engine_run_options(
                    cfg=cfg,
                    room_id=room_id,
                    thread_root_event_id=thread_root_event_id,
                    engine=job.resume_token.engine,
                )
                session_scope = session_scopes_by_msg.pop(
                    (str(job.chat_id), str(job.user_msg_id)),
                    None,
                )
                await run_job(
                    room_id,
                    str(job.user_msg_id),
                    job.text,
                    job.resume_token,
                    job.context,
                    None,
                    scheduler.note_thread_known,
                    None,
                    session_scope,
                    run_options,
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
                session_scope = _SessionScope(
                    room_id=room_id,
                    sender=msg.sender,
                    thread_root_event_id=msg.thread_root_event_id,
                )
                reply_ref = (
                    MessageRef(channel_id=room_id, message_id=reply_to)
                    if reply_to is not None
                    else None
                )
                ambient_context = await _resolve_ambient_context(
                    cfg=cfg,
                    room_id=room_id,
                    thread_root_event_id=msg.thread_root_event_id,
                )

                await cfg.client.send_read_receipt(room_id, event_id)

                # Check trigger mode for this room
                trigger_mode = await resolve_trigger_mode(
                    room_id=room_id,
                    room_prefs=cfg.room_prefs,
                    thread_root_event_id=msg.thread_root_event_id,
                    thread_state=cfg.thread_state,
                )
                if trigger_mode == "mentions":
                    # Determine if message is a reply to a bot message.
                    reply_to_is_bot = await _is_reply_to_bot_message(
                        room_id=room_id,
                        reply_to_event_id=reply_to,
                        own_user_id=own_user_id,
                        running_tasks=running_tasks,
                        cfg=cfg,
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
                if command_id in BUILTIN_COMMAND_IDS:
                    await handle_builtin_command(
                        cfg,
                        msg,
                        command_id=command_id,
                        args_text=args_text,
                        ambient_context=ambient_context,
                    )
                    continue
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
                            _wrap_on_thread_known(
                                cfg=cfg,
                                scope=session_scope,
                                base_cb=scheduler.note_thread_known,
                            ),
                        )
                        continue

                reply_text = msg.reply_to_text
                try:
                    resolved = cfg.runtime.resolve_message(
                        text=text,
                        reply_text=reply_text,
                        ambient_context=ambient_context,
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
                context = resolved.context

                if (
                    msg.thread_root_event_id is not None
                    and cfg.thread_state is not None
                    and context is not None
                    and resolved.context_source == "directives"
                ):
                    await cfg.thread_state.set_context(
                        room_id, msg.thread_root_event_id, context
                    )

                # Resolve engine using hierarchy:
                # 1. Directive (@engine), 2. Room default, 3. Project default, 4. Global
                engine_resolution = await resolve_engine_for_message(
                    runtime=cfg.runtime,
                    context=context,
                    explicit_engine=resolved.engine_override,
                    room_id=room_id,
                    room_prefs=cfg.room_prefs,
                    thread_root_event_id=msg.thread_root_event_id,
                    thread_state=cfg.thread_state,
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

                        async def enqueue_resume_with_scope(
                            queued_room_id: str,
                            queued_event_id: str,
                            queued_text: str,
                            queued_resume: ResumeToken,
                            queued_context,
                            _session_scope: _SessionScope = session_scope,
                            _thread_root_event_id: str
                            | None = msg.thread_root_event_id,
                        ) -> None:
                            session_scopes_by_msg[(queued_room_id, queued_event_id)] = (
                                _session_scope
                            )
                            await scheduler.enqueue_resume(
                                queued_room_id,
                                queued_event_id,
                                queued_text,
                                queued_resume,
                                queued_context,
                                _thread_root_event_id,
                            )

                        tg.start_soon(
                            _send_with_resume,
                            cfg,
                            enqueue_resume_with_scope,
                            running_task,
                            room_id,
                            event_id,
                            text,
                        )
                        continue

                if _should_warn_reply_resume_fallback(
                    msg=msg,
                    resume_token=resume_token,
                ):
                    await _send_plain(
                        cfg.exec_cfg,
                        room_id=room_id,
                        reply_to_event_id=event_id,
                        text=(
                            "warning: couldn't read the replied message for precise "
                            "resume; using current chat/session context."
                        ),
                    )

                if resume_token is None:
                    resume_token = await _lookup_session_resume(
                        cfg=cfg,
                        scope=session_scope,
                        engine=engine_override,
                    )

                if resume_token is None:
                    run_options = await _resolve_engine_run_options(
                        cfg=cfg,
                        room_id=room_id,
                        thread_root_event_id=msg.thread_root_event_id,
                        engine=engine_override,
                    )
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
                        session_scope,
                        run_options,
                    )
                else:
                    session_scopes_by_msg[(room_id, event_id)] = session_scope
                    await scheduler.enqueue_resume(
                        room_id,
                        event_id,
                        text,
                        resume_token,
                        context,
                        msg.thread_root_event_id,
                    )

    finally:
        await cfg.exec_cfg.transport.close()
