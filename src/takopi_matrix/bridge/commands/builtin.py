"""Built-in Matrix transport commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from takopi.api import (
    ConfigError,
    DirectiveError,
    MessageRef,
    RenderedMessage,
    RunContext,
    SendOptions,
)
from takopi.telegram.files import (
    ZipTooLargeError,
    deny_reason,
    format_bytes,
    normalize_relative_path,
    parse_file_command,
    parse_file_prompt,
    resolve_path_within_root,
    write_bytes_atomic,
    zip_directory,
)

from ...engine_defaults import EngineResolution, resolve_engine_for_message
from ...engine_overrides import (
    EngineOverrides,
    allowed_reasoning_levels,
    resolve_override_value,
    supports_reasoning,
)
from ...trigger_mode import resolve_trigger_mode
from ...types import MatrixIncomingMessage
from .parse import split_command_args

if TYPE_CHECKING:
    from ..config import MatrixBridgeConfig

BUILTIN_COMMAND_IDS = frozenset(
    {
        "ctx",
        "new",
        "agent",
        "model",
        "reasoning",
        "trigger",
        "file",
        "reload",
    }
)

CTX_USAGE = "usage: `/ctx`, `/ctx set <project> [@branch]`, or `/ctx clear`"
AGENT_USAGE = "usage: `/agent`, `/agent set <engine>`, or `/agent clear`"
MODEL_USAGE = (
    "usage: `/model`, `/model set <model>`, "
    "`/model set <engine> <model>`, or `/model clear [engine]`"
)
REASONING_USAGE = (
    "usage: `/reasoning`, `/reasoning set <level>`, "
    "`/reasoning set <engine> <level>`, or `/reasoning clear [engine]`"
)
TRIGGER_USAGE = (
    "usage: `/trigger`, `/trigger all`, `/trigger mentions`, or `/trigger clear`"
)
FILE_PUT_USAGE = "usage: `/file put <path>`"
FILE_GET_USAGE = "usage: `/file get <path>`"
FILE_DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
)
FILE_DEFAULT_UPLOADS_DIR = "uploads"

ENGINE_SOURCE_LABELS = {
    "directive": "directive",
    "thread_default": "thread default",
    "room_default": "room default",
    "project_default": "project default",
    "global_default": "global default",
}
OVERRIDE_SOURCE_LABELS = {
    "thread_override": "thread override",
    "room_default": "room default",
    "default": "default",
}


@dataclass(frozen=True, slots=True)
class CtxSetParseResult:
    context: RunContext | None
    error: str | None


@dataclass(frozen=True, slots=True)
class OverrideSetArgs:
    engine: str | None
    value: str | None


def _request_process_restart() -> None:
    """Request process restart via graceful shutdown path.

    Raises ``KeyboardInterrupt`` so the exception propagates up to
    ``anyio.run()``, which triggers the ``finally`` block in
    ``run_main_loop`` for cleanup (state save, client close).

    An external process manager (Docker restart policy, systemd, etc.)
    is expected to restart the process after exit.
    """
    raise KeyboardInterrupt


async def _reply(
    cfg: MatrixBridgeConfig,
    *,
    room_id: str,
    event_id: str,
    text: str,
    notify: bool = True,
) -> None:
    reply_to = MessageRef(channel_id=room_id, message_id=event_id)
    await cfg.exec_cfg.transport.send(
        channel_id=room_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=notify),
    )


async def _require_admin_or_private(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    *,
    missing_sender: str,
    failed_member: str,
    denied: str,
    allow_private: bool = True,
) -> bool:
    sender = msg.sender.strip() if isinstance(msg.sender, str) else ""
    if not sender:
        await _reply(
            cfg,
            room_id=msg.room_id,
            event_id=msg.event_id,
            text=missing_sender,
        )
        return False

    # If allowlist is configured, it is authoritative.
    if cfg.user_allowlist is not None:
        if sender in cfg.user_allowlist:
            return True
        await _reply(
            cfg,
            room_id=msg.room_id,
            event_id=msg.event_id,
            text=denied,
        )
        return False

    if allow_private:
        is_direct = await cfg.client.is_direct_room(msg.room_id)
        if is_direct is True:
            return True

    is_admin = await cfg.client.is_room_admin(msg.room_id, sender)
    if is_admin is None:
        await _reply(
            cfg,
            room_id=msg.room_id,
            event_id=msg.event_id,
            text=failed_member,
        )
        return False
    if is_admin:
        return True
    await _reply(
        cfg,
        room_id=msg.room_id,
        event_id=msg.event_id,
        text=denied,
    )
    return False


def _format_context(runtime, context: RunContext | None) -> str:
    if context is None or context.project is None:
        return "none"
    project = runtime.project_alias_for_key(context.project)
    if context.branch:
        return f"{project} @{context.branch}"
    return project


def _parse_ctx_set_args(
    *,
    args_text: str,
    runtime,
    default_project: str | None,
) -> CtxSetParseResult:
    tokens = split_command_args(args_text)
    if not tokens:
        return CtxSetParseResult(None, CTX_USAGE)
    if len(tokens) > 2:
        return CtxSetParseResult(None, "too many arguments")

    project_token: str | None = None
    branch: str | None = None
    first = tokens[0]
    if first.startswith("@"):
        branch = first[1:] or None
    else:
        project_token = first
        if len(tokens) == 2:
            second = tokens[1]
            if not second.startswith("@"):
                return CtxSetParseResult(None, "branch must be prefixed with @")
            branch = second[1:] or None

    if project_token is None:
        if default_project is None:
            return CtxSetParseResult(None, "project is required")
        project_key = default_project
    else:
        project_key = runtime.normalize_project_key(project_token)
        if project_key is None:
            return CtxSetParseResult(None, f"unknown project {project_token!r}")

    return CtxSetParseResult(RunContext(project=project_key, branch=branch), None)


def _parse_override_set_args(
    tokens: tuple[str, ...], *, engine_ids: set[str]
) -> OverrideSetArgs:
    # tokens include the "set" action at index 0.
    if len(tokens) == 2:
        return OverrideSetArgs(engine=None, value=tokens[1])
    if len(tokens) == 3:
        engine = tokens[1].strip().lower()
        if engine in engine_ids:
            return OverrideSetArgs(engine=engine, value=tokens[2])
    return OverrideSetArgs(engine=None, value=None)


def _thread_scope(msg: MatrixIncomingMessage, cfg: MatrixBridgeConfig) -> str | None:
    if msg.thread_root_event_id is None:
        return None
    if cfg.thread_state is None:
        return None
    return msg.thread_root_event_id


async def _resolve_engine_selection(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    *,
    ambient_context: RunContext | None,
) -> EngineResolution:
    return await resolve_engine_for_message(
        runtime=cfg.runtime,
        context=ambient_context,
        explicit_engine=None,
        room_id=msg.room_id,
        room_prefs=cfg.room_prefs,
        thread_root_event_id=msg.thread_root_event_id,
        thread_state=cfg.thread_state,
        room_project_map=cfg.room_project_map,
    )


async def _read_overrides_for_engine(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    engine: str,
) -> tuple[EngineOverrides | None, EngineOverrides | None]:
    thread_override = None
    thread_root = _thread_scope(msg, cfg)
    if thread_root is not None:
        thread_override = await cfg.thread_state.get_engine_override(
            msg.room_id, thread_root, engine
        )
    room_override = None
    if cfg.room_prefs is not None:
        room_override = await cfg.room_prefs.get_engine_override(msg.room_id, engine)
    return thread_override, room_override


async def _apply_override_update(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    *,
    engine: str,
    update: Callable[[EngineOverrides | None], EngineOverrides | None],
) -> Literal["thread", "room"] | None:
    thread_root = _thread_scope(msg, cfg)
    if thread_root is not None:
        current = await cfg.thread_state.get_engine_override(
            msg.room_id, thread_root, engine
        )
        await cfg.thread_state.set_engine_override(
            msg.room_id, thread_root, engine, update(current)
        )
        return "thread"
    if cfg.room_prefs is None:
        return None
    current = await cfg.room_prefs.get_engine_override(msg.room_id, engine)
    await cfg.room_prefs.set_engine_override(msg.room_id, engine, update(current))
    return "room"


async def _handle_ctx_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> None:
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    room_id = msg.room_id
    event_id = msg.event_id
    thread_root = _thread_scope(msg, cfg)

    if action in {"show", ""}:
        if thread_root is not None:
            bound = await cfg.thread_state.get_context(room_id, thread_root)
            scope = "thread"
        elif cfg.room_prefs is not None:
            bound = await cfg.room_prefs.get_context(room_id)
            scope = "room"
        else:
            bound = None
            scope = "room"
        resolved = cfg.runtime.resolve_message(
            text="",
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
        )
        source = (
            "bound"
            if bound is not None and resolved.context_source == "ambient"
            else resolved.context_source
        )
        lines = [
            f"scope: {scope}",
            f"bound ctx: {_format_context(cfg.runtime, bound)}",
            f"resolved ctx: {_format_context(cfg.runtime, resolved.context)} (source: {source})",
        ]
        if bound is None:
            lines.append("note: no bound context for this scope")
        await _reply(cfg, room_id=room_id, event_id=event_id, text="\n".join(lines))
        return

    if action == "set":
        default_project = (
            ambient_context.project if ambient_context is not None else None
        )
        parsed = _parse_ctx_set_args(
            args_text=" ".join(tokens[1:]),
            runtime=cfg.runtime,
            default_project=default_project,
        )
        if parsed.error is not None or parsed.context is None:
            suffix = f"\n{CTX_USAGE}" if parsed.error != CTX_USAGE else ""
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"error:\n{parsed.error}{suffix}",
            )
            return
        if thread_root is not None:
            await cfg.thread_state.set_context(room_id, thread_root, parsed.context)
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"thread bound to `{_format_context(cfg.runtime, parsed.context)}`",
            )
            return
        if cfg.room_prefs is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="room context store unavailable.",
            )
            return
        await cfg.room_prefs.set_context(room_id, parsed.context)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"room bound to `{_format_context(cfg.runtime, parsed.context)}`",
        )
        return

    if action == "clear":
        if thread_root is not None:
            await cfg.thread_state.clear_context(room_id, thread_root)
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="thread context cleared.",
            )
            return
        if cfg.room_prefs is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="room context store unavailable.",
            )
            return
        await cfg.room_prefs.clear_context(room_id)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="room context cleared.",
        )
        return

    await _reply(
        cfg,
        room_id=room_id,
        event_id=event_id,
        text="unknown `/ctx` command. use `/ctx`, `/ctx set`, or `/ctx clear`.",
    )


async def _handle_new_command(
    cfg: MatrixBridgeConfig, msg: MatrixIncomingMessage
) -> None:
    room_id = msg.room_id
    event_id = msg.event_id
    thread_root = _thread_scope(msg, cfg)
    if thread_root is not None:
        await cfg.thread_state.clear_sessions(room_id, thread_root)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="cleared stored sessions for this thread.",
        )
        return
    if cfg.chat_sessions is None:
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="no stored sessions to clear for this room.",
        )
        return
    await cfg.chat_sessions.clear_sessions(room_id, msg.sender)
    await _reply(
        cfg,
        room_id=room_id,
        event_id=event_id,
        text="cleared stored sessions for you in this room.",
    )


async def _handle_agent_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> None:
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    room_id = msg.room_id
    event_id = msg.event_id
    thread_root = _thread_scope(msg, cfg)

    if action in {"show", ""}:
        selection = await _resolve_engine_selection(
            cfg, msg, ambient_context=ambient_context
        )
        lines = [
            f"engine: {selection.engine} ({ENGINE_SOURCE_LABELS[selection.source]})",
            "defaults: "
            f"thread: {selection.thread_default or 'none'}, "
            f"room: {selection.room_default or 'none'}, "
            f"project: {selection.project_default or 'none'}, "
            f"global: {cfg.runtime.default_engine}",
            f"available: {', '.join(cfg.runtime.engine_ids)}",
        ]
        await _reply(cfg, room_id=room_id, event_id=event_id, text="\n\n".join(lines))
        return

    if action == "set":
        if len(tokens) < 2:
            await _reply(cfg, room_id=room_id, event_id=event_id, text=AGENT_USAGE)
            return
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for engine defaults.",
            failed_member="failed to verify engine permissions.",
            denied="changing default engines is restricted to room admins.",
        ):
            return
        engine = tokens[1].strip().lower()
        if engine not in cfg.runtime.engine_ids:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"unknown engine `{engine}`.\navailable: `{', '.join(cfg.runtime.engine_ids)}`",
            )
            return
        if thread_root is not None:
            await cfg.thread_state.set_default_engine(room_id, thread_root, engine)
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"thread default engine set to `{engine}`",
            )
            return
        if cfg.room_prefs is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="room defaults store unavailable.",
            )
            return
        await cfg.room_prefs.set_default_engine(room_id, engine)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"room default engine set to `{engine}`",
        )
        return

    if action == "clear":
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for engine defaults.",
            failed_member="failed to verify engine permissions.",
            denied="changing default engines is restricted to room admins.",
        ):
            return
        if thread_root is not None:
            await cfg.thread_state.clear_default_engine(room_id, thread_root)
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="thread default engine cleared.",
            )
            return
        if cfg.room_prefs is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="room defaults store unavailable.",
            )
            return
        await cfg.room_prefs.clear_default_engine(room_id)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="room default engine cleared.",
        )
        return

    await _reply(cfg, room_id=room_id, event_id=event_id, text=AGENT_USAGE)


async def _handle_model_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> None:
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    room_id = msg.room_id
    event_id = msg.event_id
    engine_ids = {engine.lower() for engine in cfg.runtime.engine_ids}

    if action in {"show", ""}:
        selection = await _resolve_engine_selection(
            cfg, msg, ambient_context=ambient_context
        )
        thread_override, room_override = await _read_overrides_for_engine(
            cfg, msg, selection.engine
        )
        resolution = resolve_override_value(
            thread_override=thread_override,
            room_override=room_override,
            field="model",
        )
        lines = [
            f"engine: {selection.engine} ({ENGINE_SOURCE_LABELS[selection.source]})",
            f"model: {resolution.value or 'default'} ({OVERRIDE_SOURCE_LABELS[resolution.source]})",
            "defaults: "
            f"thread: {resolution.thread_value or 'none'}, "
            f"room: {resolution.room_value or 'none'}",
            f"available engines: {', '.join(cfg.runtime.engine_ids)}",
        ]
        await _reply(cfg, room_id=room_id, event_id=event_id, text="\n\n".join(lines))
        return

    if action == "set":
        parsed = _parse_override_set_args(tokens, engine_ids=engine_ids)
        if parsed.value is None:
            await _reply(cfg, room_id=room_id, event_id=event_id, text=MODEL_USAGE)
            return
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for model overrides.",
            failed_member="failed to verify model override permissions.",
            denied="changing model overrides is restricted to room admins.",
        ):
            return
        if parsed.engine is None:
            selection = await _resolve_engine_selection(
                cfg, msg, ambient_context=ambient_context
            )
            engine = selection.engine
        else:
            engine = parsed.engine
        scope = await _apply_override_update(
            cfg,
            msg,
            engine=engine,
            update=lambda current: EngineOverrides(
                model=parsed.value,
                reasoning=current.reasoning if current is not None else None,
            ),
        )
        if scope is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="override store unavailable.",
            )
            return
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"{scope} model override set to `{parsed.value}` for `{engine}`.\nIf you want a clean start, run `/new`.",
        )
        return

    if action == "clear":
        if len(tokens) > 2:
            await _reply(cfg, room_id=room_id, event_id=event_id, text=MODEL_USAGE)
            return
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for model overrides.",
            failed_member="failed to verify model override permissions.",
            denied="changing model overrides is restricted to room admins.",
        ):
            return
        engine = tokens[1].strip().lower() if len(tokens) == 2 else None
        if engine is None:
            selection = await _resolve_engine_selection(
                cfg, msg, ambient_context=ambient_context
            )
            engine = selection.engine
        if engine not in engine_ids:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"unknown engine `{engine}`.\navailable: `{', '.join(cfg.runtime.engine_ids)}`",
            )
            return
        scope = await _apply_override_update(
            cfg,
            msg,
            engine=engine,
            update=lambda current: EngineOverrides(
                model=None,
                reasoning=current.reasoning if current is not None else None,
            ),
        )
        if scope is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="override store unavailable.",
            )
            return
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"{scope} model override cleared.",
        )
        return

    await _reply(cfg, room_id=room_id, event_id=event_id, text=MODEL_USAGE)


async def _handle_reasoning_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> None:
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    room_id = msg.room_id
    event_id = msg.event_id
    engine_ids = {engine.lower() for engine in cfg.runtime.engine_ids}

    if action in {"show", ""}:
        selection = await _resolve_engine_selection(
            cfg, msg, ambient_context=ambient_context
        )
        thread_override, room_override = await _read_overrides_for_engine(
            cfg, msg, selection.engine
        )
        resolution = resolve_override_value(
            thread_override=thread_override,
            room_override=room_override,
            field="reasoning",
        )
        lines = [
            f"engine: {selection.engine} ({ENGINE_SOURCE_LABELS[selection.source]})",
            "reasoning: "
            f"{resolution.value or 'default'} ({OVERRIDE_SOURCE_LABELS[resolution.source]})",
            "defaults: "
            f"thread: {resolution.thread_value or 'none'}, "
            f"room: {resolution.room_value or 'none'}",
            f"available levels: {', '.join(allowed_reasoning_levels())}",
        ]
        await _reply(cfg, room_id=room_id, event_id=event_id, text="\n\n".join(lines))
        return

    if action == "set":
        parsed = _parse_override_set_args(tokens, engine_ids=engine_ids)
        if parsed.value is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=REASONING_USAGE,
            )
            return
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for reasoning overrides.",
            failed_member="failed to verify reasoning override permissions.",
            denied="changing reasoning overrides is restricted to room admins.",
        ):
            return
        if parsed.engine is None:
            selection = await _resolve_engine_selection(
                cfg, msg, ambient_context=ambient_context
            )
            engine = selection.engine
        else:
            engine = parsed.engine
        if not supports_reasoning(engine):
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"engine `{engine}` does not support reasoning overrides.",
            )
            return
        level = parsed.value.strip().lower()
        allowed = allowed_reasoning_levels()
        if level not in allowed:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"unknown reasoning level `{parsed.value}`.\navailable: {', '.join(allowed)}",
            )
            return
        scope = await _apply_override_update(
            cfg,
            msg,
            engine=engine,
            update=lambda current: EngineOverrides(
                model=current.model if current is not None else None,
                reasoning=level,
            ),
        )
        if scope is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="override store unavailable.",
            )
            return
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"{scope} reasoning override set to `{level}` for `{engine}`.\nIf you want a clean start, run `/new`.",
        )
        return

    if action == "clear":
        if len(tokens) > 2:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=REASONING_USAGE,
            )
            return
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for reasoning overrides.",
            failed_member="failed to verify reasoning override permissions.",
            denied="changing reasoning overrides is restricted to room admins.",
        ):
            return
        engine = tokens[1].strip().lower() if len(tokens) == 2 else None
        if engine is None:
            selection = await _resolve_engine_selection(
                cfg, msg, ambient_context=ambient_context
            )
            engine = selection.engine
        if engine not in engine_ids:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"unknown engine `{engine}`.\navailable: `{', '.join(cfg.runtime.engine_ids)}`",
            )
            return
        scope = await _apply_override_update(
            cfg,
            msg,
            engine=engine,
            update=lambda current: EngineOverrides(
                model=current.model if current is not None else None,
                reasoning=None,
            ),
        )
        if scope is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="override store unavailable.",
            )
            return
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"{scope} reasoning override cleared.",
        )
        return

    await _reply(cfg, room_id=room_id, event_id=event_id, text=REASONING_USAGE)


async def _handle_trigger_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
) -> None:
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "show"
    room_id = msg.room_id
    event_id = msg.event_id
    thread_root = _thread_scope(msg, cfg)

    if action in {"show", ""}:
        resolved = await resolve_trigger_mode(
            room_id=room_id,
            room_prefs=cfg.room_prefs,
            thread_root_event_id=msg.thread_root_event_id,
            thread_state=cfg.thread_state,
        )
        thread_mode = (
            await cfg.thread_state.get_trigger_mode(room_id, thread_root)
            if thread_root is not None
            else None
        )
        room_mode = (
            await cfg.room_prefs.get_trigger_mode(room_id)
            if cfg.room_prefs is not None
            else None
        )
        source = (
            "thread override"
            if thread_mode is not None
            else "room default"
            if room_mode is not None
            else "default"
        )
        lines = [
            f"trigger: {resolved} ({source})",
            f"defaults: thread: {thread_mode or 'none'}, room: {room_mode or 'none'}",
            "available: all, mentions",
        ]
        await _reply(cfg, room_id=room_id, event_id=event_id, text="\n\n".join(lines))
        return

    if action in {"all", "mentions"}:
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for trigger settings.",
            failed_member="failed to verify trigger permissions.",
            denied="changing trigger mode is restricted to room admins.",
        ):
            return
        if thread_root is not None:
            mode = action if action == "mentions" else None
            await cfg.thread_state.set_trigger_mode(room_id, thread_root, mode)
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"thread trigger mode set to `{action}`",
            )
            return
        if cfg.room_prefs is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="room trigger settings unavailable.",
            )
            return
        mode = action if action == "mentions" else None
        await cfg.room_prefs.set_trigger_mode(room_id, mode)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"room trigger mode set to `{action}`",
        )
        return

    if action == "clear":
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for trigger settings.",
            failed_member="failed to verify trigger permissions.",
            denied="changing trigger mode is restricted to room admins.",
        ):
            return
        if thread_root is not None:
            await cfg.thread_state.clear_trigger_mode(room_id, thread_root)
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="thread trigger mode cleared (using room default).",
            )
            return
        if cfg.room_prefs is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="room trigger settings unavailable.",
            )
            return
        await cfg.room_prefs.clear_trigger_mode(room_id)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="room trigger mode reset to `all`.",
        )
        return

    await _reply(cfg, room_id=room_id, event_id=event_id, text=TRIGGER_USAGE)


def _file_limits(cfg: MatrixBridgeConfig) -> int:
    if cfg.file_download is None:
        return 50 * 1024 * 1024
    return max(1, cfg.file_download.max_size_bytes)


def _resolve_file_put_paths(
    *,
    path_value: str | None,
    require_dir: bool,
) -> tuple[Path | None, Path | None, str | None]:
    if not path_value:
        return None, None, None
    if require_dir or path_value.endswith("/"):
        base_dir = normalize_relative_path(path_value)
        if base_dir is None:
            return None, None, "invalid upload path."
        return base_dir, None, None
    rel_path = normalize_relative_path(path_value)
    if rel_path is None:
        return None, None, "invalid upload path."
    return None, rel_path, None


async def _resolve_file_context(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> tuple[RunContext | None, Path | None, str | None]:
    try:
        resolved = cfg.runtime.resolve_message(
            text=args_text,
            reply_text=msg.reply_to_text,
            ambient_context=ambient_context,
        )
    except DirectiveError as exc:
        return None, None, f"error:\n{exc}"
    context = resolved.context
    if context is None or context.project is None:
        return None, None, "no project context available for file command."
    try:
        run_root = cfg.runtime.resolve_run_cwd(context)
    except ConfigError as exc:
        return None, None, f"error:\n{exc}"
    if run_root is None:
        return None, None, "no project context available for file command."
    return context, run_root, None


async def _handle_file_put_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> None:
    room_id = msg.room_id
    event_id = msg.event_id
    if not await _require_admin_or_private(
        cfg,
        msg,
        missing_sender="cannot verify sender for file transfer.",
        failed_member="failed to verify file transfer permissions.",
        denied="file transfer is restricted to room admins.",
    ):
        return
    if not msg.attachments:
        await _reply(cfg, room_id=room_id, event_id=event_id, text=FILE_PUT_USAGE)
        return

    context, run_root, error = await _resolve_file_context(
        cfg,
        msg,
        args_text,
        ambient_context,
    )
    if error is not None or run_root is None or context is None:
        await _reply(cfg, room_id=room_id, event_id=event_id, text=error or "error")
        return

    prompt_value, force, parse_error = parse_file_prompt(args_text, allow_empty=True)
    if parse_error is not None:
        await _reply(cfg, room_id=room_id, event_id=event_id, text=parse_error)
        return

    base_dir, rel_path, path_error = _resolve_file_put_paths(
        path_value=prompt_value,
        require_dir=len(msg.attachments) > 1,
    )
    if path_error is not None:
        await _reply(cfg, room_id=room_id, event_id=event_id, text=path_error)
        return

    max_bytes = _file_limits(cfg)
    saved: list[tuple[str, int]] = []
    failed: list[str] = []
    for attachment in msg.attachments:
        payload = await cfg.client.download_file(
            attachment.mxc_url,
            max_size=max_bytes,
            file_info=attachment.file_info,
        )
        if payload is None:
            failed.append(f"`{attachment.filename}` (failed to download)")
            continue
        if len(payload) > max_bytes:
            failed.append(f"`{attachment.filename}` (file is too large)")
            continue
        if rel_path is not None:
            target_rel = rel_path
        elif base_dir is not None:
            target_rel = base_dir / attachment.filename
        else:
            target_rel = Path(FILE_DEFAULT_UPLOADS_DIR) / attachment.filename
        deny = deny_reason(target_rel, FILE_DEFAULT_DENY_GLOBS)
        if deny is not None:
            failed.append(f"`{attachment.filename}` (path denied by rule: {deny})")
            continue
        target = resolve_path_within_root(run_root, target_rel)
        if target is None:
            failed.append(f"`{attachment.filename}` (path escapes repo root)")
            continue
        if target.exists():
            if target.is_dir():
                failed.append(f"`{attachment.filename}` (target is directory)")
                continue
            if not force:
                failed.append(
                    f"`{attachment.filename}` (file exists; use --force to overwrite)"
                )
                continue
        try:
            write_bytes_atomic(target, payload)
            saved.append((target_rel.as_posix(), len(payload)))
        except OSError as exc:
            failed.append(f"`{attachment.filename}` (failed to write: {exc})")

    if not saved and failed:
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"failed: {', '.join(failed)}",
        )
        return
    details = ", ".join(f"`{path}` ({format_bytes(size)})" for path, size in saved)
    text = (
        f"saved {details} in `{_format_context(cfg.runtime, context)}`"
        if details
        else "nothing saved."
    )
    if failed:
        text = f"{text}\n\nfailed: {', '.join(failed)}"
    await _reply(cfg, room_id=room_id, event_id=event_id, text=text)


async def _handle_file_get_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
) -> None:
    room_id = msg.room_id
    event_id = msg.event_id
    if not await _require_admin_or_private(
        cfg,
        msg,
        missing_sender="cannot verify sender for file transfer.",
        failed_member="failed to verify file transfer permissions.",
        denied="file transfer is restricted to room admins.",
    ):
        return
    context, run_root, error = await _resolve_file_context(
        cfg,
        msg,
        args_text,
        ambient_context,
    )
    if error is not None or run_root is None or context is None:
        await _reply(cfg, room_id=room_id, event_id=event_id, text=error or "error")
        return

    path_value, _, parse_error = parse_file_prompt(args_text, allow_empty=False)
    if parse_error is not None or path_value is None:
        await _reply(cfg, room_id=room_id, event_id=event_id, text=FILE_GET_USAGE)
        return
    rel_path = normalize_relative_path(path_value)
    if rel_path is None:
        await _reply(cfg, room_id=room_id, event_id=event_id, text="invalid path.")
        return
    deny = deny_reason(rel_path, FILE_DEFAULT_DENY_GLOBS)
    if deny is not None:
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"path denied by rule: {deny}",
        )
        return

    target = resolve_path_within_root(run_root, rel_path)
    if target is None:
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="download path escapes repo root.",
        )
        return
    if not target.exists():
        await _reply(
            cfg, room_id=room_id, event_id=event_id, text="file does not exist."
        )
        return

    max_bytes = _file_limits(cfg)
    if target.is_dir():
        try:
            payload = zip_directory(
                run_root,
                rel_path,
                FILE_DEFAULT_DENY_GLOBS,
                max_bytes=max_bytes,
            )
        except ZipTooLargeError:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="file is too large to send.",
            )
            return
        except OSError as exc:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"failed to read directory: {exc}",
            )
            return
        filename = f"{rel_path.name or 'archive'}.zip"
        mimetype = "application/zip"
    else:
        try:
            size = target.stat().st_size
            if size > max_bytes:
                await _reply(
                    cfg,
                    room_id=room_id,
                    event_id=event_id,
                    text="file is too large to send.",
                )
                return
            payload = target.read_bytes()
        except OSError as exc:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"failed to read file: {exc}",
            )
            return
        filename = target.name
        mimetype = None

    sent = await cfg.client.send_file(
        room_id,
        filename=filename,
        payload=payload,
        mimetype=mimetype,
        reply_to_event_id=event_id,
        encrypt=True,
    )
    if sent is None:
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="failed to send file.",
        )


async def handle_builtin_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    *,
    command_id: str,
    args_text: str,
    ambient_context: RunContext | None,
) -> bool:
    """Handle built-in Matrix transport commands."""
    if command_id == "ctx":
        await _handle_ctx_command(cfg, msg, args_text, ambient_context)
        return True
    if command_id == "new":
        await _handle_new_command(cfg, msg)
        return True
    if command_id == "agent":
        await _handle_agent_command(cfg, msg, args_text, ambient_context)
        return True
    if command_id == "model":
        await _handle_model_command(cfg, msg, args_text, ambient_context)
        return True
    if command_id == "reasoning":
        await _handle_reasoning_command(cfg, msg, args_text, ambient_context)
        return True
    if command_id == "trigger":
        await _handle_trigger_command(cfg, msg, args_text)
        return True
    if command_id == "file":
        subcommand, rest, error = parse_file_command(args_text)
        if error is not None:
            await _reply(cfg, room_id=msg.room_id, event_id=msg.event_id, text=error)
            return True
        if subcommand == "put":
            await _handle_file_put_command(cfg, msg, rest, ambient_context)
            return True
        if subcommand == "get":
            await _handle_file_get_command(cfg, msg, rest, ambient_context)
            return True
        return True
    if command_id == "reload":
        if not await _require_admin_or_private(
            cfg,
            msg,
            missing_sender="cannot verify sender for reload command.",
            failed_member="failed to verify reload permissions.",
            denied="reload is restricted to room admins or allowlisted users.",
            allow_private=False,
        ):
            return True
        await _reply(
            cfg,
            room_id=msg.room_id,
            event_id=msg.event_id,
            text="reload requested. restarting takopi process now.",
        )
        _request_process_restart()
        return True
    return False
