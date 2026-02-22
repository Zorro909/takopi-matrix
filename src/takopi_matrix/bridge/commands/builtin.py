"""Built-in Matrix transport commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Literal, cast

import anyio
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
        "repo",
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
REPO_USAGE = (
    "usage: `/repo list`, `/repo add <alias> <git_url>`, `/repo bind <alias>`, "
    "`/repo fetch [alias]`"
)
FILE_DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
)
FILE_DEFAULT_UPLOADS_DIR = "uploads"
REPO_ROOT = Path("/workspace/repos")
WORKTREE_ROOT = Path("/workspace/worktrees")
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ALLOWED_GIT_SCHEMES = ("https://", "http://", "git://", "ssh://", "git@")


def _validate_git_url(url: str) -> str | None:
    """Return an error message if *url* is not a safe git URL, else None."""
    if not url:
        return "empty URL"
    if url.startswith("-"):
        return "URL cannot start with '-'"
    if url.lower().startswith("file://"):
        return "file:// protocol not allowed"
    if not any(url.startswith(s) for s in _ALLOWED_GIT_SCHEMES):
        return f"URL must start with one of: {', '.join(_ALLOWED_GIT_SCHEMES)}"


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


def _format_process_error(stderr: bytes | str | None) -> str:
    if isinstance(stderr, bytes):
        text = stderr.decode("utf-8", errors="replace").strip()
    elif isinstance(stderr, str):
        text = stderr.strip()
    else:
        text = ""
    return text or "command failed"


async def _save_config_toml(path: Path, document) -> None:
    import tomlkit

    temp_path = path.with_suffix(".toml.tmp")
    temp_path.write_text(tomlkit.dumps(document))
    temp_path.rename(path)


async def _update_room_binding(path: Path, *, room_id: str, project: str) -> None:
    import tomlkit

    document = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    if "transports" not in document:
        document["transports"] = tomlkit.table()
    transports = cast("dict[str, Any]", document["transports"])
    if "matrix" not in transports:
        transports["matrix"] = tomlkit.table()
    matrix = cast("dict[str, Any]", transports["matrix"])
    if "room_projects" not in matrix:
        matrix["room_projects"] = tomlkit.table()
    cast("dict[str, Any]", matrix["room_projects"])[room_id] = project
    await _save_config_toml(path, document)


async def _update_project_config(
    path: Path,
    *,
    alias: str,
    repo_path: Path,
    worktrees_path: Path,
) -> None:
    import tomlkit

    document = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    if "projects" not in document:
        document["projects"] = tomlkit.table()
    projects = cast("dict[str, Any]", document["projects"])
    if alias not in projects:
        projects[alias] = tomlkit.table()
    proj = cast("dict[str, Any]", projects[alias])
    proj["path"] = str(repo_path)
    proj["worktrees_dir"] = str(worktrees_path)
    if "worktree_base" not in proj:
        proj["worktree_base"] = "main"
    await _save_config_toml(path, document)


async def _repo_clone_or_fetch(alias: str, git_url: str) -> tuple[bool, str]:
    repo_dir = REPO_ROOT / alias
    if repo_dir.exists():
        if (repo_dir / ".git").exists():
            result = await anyio.run_process(
                ["git", "-C", str(repo_dir), "fetch", "--all", "--prune"],
                check=False,
            )
            if result.returncode == 0:
                return True, f"updated existing repo at `{repo_dir}`"
            return False, _format_process_error(result.stderr)
        return False, f"target path exists and is not a git repo: `{repo_dir}`"
    if not git_url:
        return False, f"repo does not exist at `{repo_dir}`"

    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    result = await anyio.run_process(
        ["git", "clone", git_url, str(repo_dir)],
        check=False,
    )
    if result.returncode == 0:
        return True, f"cloned `{git_url}` to `{repo_dir}`"
    return False, _format_process_error(result.stderr)


async def _repo_fetch_only(repo_dir: Path) -> tuple[bool, str]:
    if not (repo_dir / ".git").exists():
        return False, f"repo not found at `{repo_dir}`"
    result = await anyio.run_process(
        ["git", "-C", str(repo_dir), "fetch", "--all", "--prune"],
        check=False,
    )
    if result.returncode == 0:
        return True, "ok"
    return False, _format_process_error(result.stderr)


def _resolve_project_alias(runtime, token: str) -> str | None:
    return runtime.normalize_project_key(token)


async def _handle_repo_command(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    args_text: str,
) -> None:
    room_id = msg.room_id
    event_id = msg.event_id
    tokens = split_command_args(args_text)
    action = tokens[0].lower() if tokens else "list"

    if action == "list":
        aliases = sorted(set(cfg.runtime.project_aliases()), key=str.lower)
        current = (
            cfg.room_project_map.project_for_room(room_id)
            if cfg.room_project_map is not None
            else None
        )
        lines = [
            f"projects: {', '.join(aliases) if aliases else 'none'}",
            f"room binding: {current or 'none'}",
        ]
        await _reply(cfg, room_id=room_id, event_id=event_id, text="\n".join(lines))
        return

    config_path = cfg.runtime.config_path
    if config_path is None:
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="config path unavailable; cannot update project config.",
        )
        return

    if action == "bind":
        if len(tokens) != 2:
            await _reply(cfg, room_id=room_id, event_id=event_id, text=REPO_USAGE)
            return
        alias = _resolve_project_alias(cfg.runtime, tokens[1])
        if alias is None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"unknown project {tokens[1]!r}",
            )
            return
        await _update_room_binding(config_path, room_id=room_id, project=alias)
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=f"bound room to `{alias}` in config. restart takopi to apply.",
        )
        return

    if action == "add":
        if len(tokens) != 3:
            await _reply(cfg, room_id=room_id, event_id=event_id, text=REPO_USAGE)
            return
        alias = tokens[1].strip()
        git_url = tokens[2].strip()
        if not ALIAS_RE.match(alias):
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="invalid alias; use letters, numbers, dot, underscore, dash.",
            )
            return
        url_error = _validate_git_url(git_url)
        if url_error is not None:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=f"invalid git URL: {url_error}",
            )
            return
        existing = set(cfg.runtime.project_aliases())
        if alias.lower() in {a.lower() for a in existing}:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text=(
                    f"project `{alias}` already exists. "
                    "use `/repo fetch` to update, or choose a different alias."
                ),
            )
            return
        ok, message = await _repo_clone_or_fetch(alias, git_url)
        if not ok:
            await _reply(
                cfg, room_id=room_id, event_id=event_id, text=f"error:\n{message}"
            )
            return
        repo_path = REPO_ROOT / alias
        worktrees_dir = WORKTREE_ROOT / alias
        await _update_project_config(
            config_path,
            alias=alias,
            repo_path=repo_path,
            worktrees_path=worktrees_dir,
        )
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text=(
                f"{message}\nproject `{alias}` written to config.\n"
                "optional next step: `/repo bind "
                f"{alias}`\nrestart takopi to apply."
            ),
        )
        return

    if action == "fetch":
        if len(tokens) > 2:
            await _reply(cfg, room_id=room_id, event_id=event_id, text=REPO_USAGE)
            return
        aliases: list[str]
        if len(tokens) == 2:
            alias = _resolve_project_alias(cfg.runtime, tokens[1])
            if alias is None:
                await _reply(
                    cfg,
                    room_id=room_id,
                    event_id=event_id,
                    text=f"unknown project {tokens[1]!r}",
                )
                return
            aliases = [alias]
        else:
            aliases = sorted(set(cfg.runtime.project_aliases()), key=str.lower)
        if not aliases:
            await _reply(
                cfg,
                room_id=room_id,
                event_id=event_id,
                text="no projects configured.",
            )
            return
        results: list[str] = []
        for alias in aliases:
            try:
                resolved = cfg.runtime.resolve_run_cwd(
                    RunContext(project=alias, branch=None)
                )
            except ConfigError:
                resolved = None
            repo_dir = resolved if resolved is not None else (REPO_ROOT / alias)
            ok, message = await _repo_fetch_only(repo_dir)
            if ok:
                results.append(f"{alias}: ok")
            else:
                results.append(f"{alias}: {message}")
        await _reply(
            cfg,
            room_id=room_id,
            event_id=event_id,
            text="\n".join(results),
        )
        return

    await _reply(cfg, room_id=room_id, event_id=event_id, text=REPO_USAGE)


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
    if command_id == "repo":
        await _handle_repo_command(cfg, msg, args_text)
        return True
    if command_id == "reload":
        await _reply(
            cfg,
            room_id=msg.room_id,
            event_id=msg.event_id,
            text="configuration changed. restart takopi/container to apply.",
        )
        return True
    return False
