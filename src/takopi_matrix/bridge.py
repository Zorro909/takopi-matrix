from __future__ import annotations

import os
import shlex
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import anyio

# Public API imports from takopi
from takopi.api import (
    CommandContext,
    CommandExecutor,
    ConfigError,
    DirectiveError,
    EngineId,
    ExecBridgeConfig,
    IncomingMessage as RunnerIncomingMessage,
    MessageRef,
    RenderedMessage,
    ResumeToken,
    RunContext,
    RunMode,
    Runner,
    RunnerUnavailableError,
    RunningTask,
    RunningTasks,
    RunRequest,
    RunResult,
    SendOptions,
    Transport,
    TransportRuntime,
    handle_message,
)

# Direct takopi module imports
from takopi.commands import get_command, list_command_ids
from takopi.ids import RESERVED_COMMAND_IDS
from takopi.logging import bind_run_context, clear_context, get_logger
from takopi.markdown import MarkdownFormatter, MarkdownParts
from takopi.progress import ProgressState, ProgressTracker
from takopi.scheduler import ThreadJob, ThreadScheduler
from takopi.utils.paths import reset_run_base_dir, set_run_base_dir
from .client import (
    MatrixClient,
    MatrixRetryAfter,
    parse_reaction,
    parse_room_audio,
    parse_room_media,
    parse_room_message,
)
from .engine_defaults import (
    EngineResolution,
    _allowed_room_ids,
    resolve_context_for_room,
    resolve_engine_for_message,
)
from .room_projects import RoomProjectMap
from .files import MAX_FILE_SIZE, process_attachments
from .render import prepare_matrix
from .room_prefs import RoomPrefsStore
from .types import MatrixIncomingMessage, MatrixReaction

logger = get_logger(__name__)

_OPENAI_AUDIO_MAX_BYTES = 25 * 1024 * 1024
_OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
_OPENAI_TRANSCRIPTION_CHUNKING = "auto"
_CANCEL_REACTIONS = frozenset({"\u274c", "x", "X"})


def _is_cancel_command(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


def _parse_slash_command(text: str) -> tuple[str | None, str]:
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


class MatrixPresenter:
    """Renders progress and final messages with Matrix HTML formatting."""

    def __init__(self, *, formatter: MarkdownFormatter | None = None) -> None:
        self._formatter = formatter or MarkdownFormatter()

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        parts = self._formatter.render_progress_parts(
            state, elapsed_s=elapsed_s, label=label
        )
        text, formatted_body = prepare_matrix(parts)
        return RenderedMessage(text=text, extra={"formatted_body": formatted_body})

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        parts = self._formatter.render_final_parts(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        text, formatted_body = prepare_matrix(parts)
        return RenderedMessage(text=text, extra={"formatted_body": formatted_body})


class MatrixTransport:
    """Implements Transport protocol for Matrix."""

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    async def close(self) -> None:
        await self._client.close()

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        room_id = str(channel_id)
        reply_to_event_id: str | None = None
        disable_notification = False

        if options is not None:
            disable_notification = not options.notify
            if options.reply_to is not None:
                reply_to_event_id = str(options.reply_to.message_id)
            if options.replace is not None:
                await self._client.drop_pending_edits(
                    room_id=room_id,
                    event_id=str(options.replace.message_id),
                )

        formatted_body = message.extra.get("formatted_body")

        sent = await self._client.send_message(
            room_id=room_id,
            body=message.text,
            formatted_body=formatted_body,
            reply_to_event_id=reply_to_event_id,
            disable_notification=disable_notification,
        )

        if sent is None:
            return None

        event_id = sent.get("event_id")
        if event_id is None:
            return None

        if options is not None and options.replace is not None:
            await self._client.redact_message(
                room_id=room_id,
                event_id=str(options.replace.message_id),
            )

        return MessageRef(
            channel_id=room_id,
            message_id=event_id,
            raw=sent,
        )

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
        wait: bool = True,
    ) -> MessageRef | None:
        room_id = str(ref.channel_id)
        event_id = str(ref.message_id)
        formatted_body = message.extra.get("formatted_body")

        edited = await self._client.edit_message(
            room_id=room_id,
            event_id=event_id,
            body=message.text,
            formatted_body=formatted_body,
            wait=wait,
        )

        if edited is None:
            return ref if not wait else None

        new_event_id = edited.get("event_id", event_id)
        return MessageRef(
            channel_id=room_id,
            message_id=new_event_id,
            raw=edited,
        )

    async def delete(self, *, ref: MessageRef) -> bool:
        return await self._client.redact_message(
            room_id=str(ref.channel_id),
            event_id=str(ref.message_id),
        )


@dataclass(frozen=True)
class MatrixVoiceTranscriptionConfig:
    enabled: bool = False


@dataclass(frozen=True)
class MatrixFileDownloadConfig:
    enabled: bool = True
    max_size_bytes: int = MAX_FILE_SIZE
    download_dir: Path | None = None


@dataclass(frozen=True)
class MatrixBridgeConfig:
    client: MatrixClient
    runtime: TransportRuntime
    room_ids: list[str]
    user_allowlist: set[str] | None
    startup_msg: str
    exec_cfg: ExecBridgeConfig
    voice_transcription: MatrixVoiceTranscriptionConfig | None = None
    file_download: MatrixFileDownloadConfig | None = None
    send_startup_message: bool = True
    room_prefs: RoomPrefsStore | None = None
    room_project_map: RoomProjectMap | None = None


async def _send_plain(
    transport: Transport,
    *,
    room_id: str,
    reply_to_event_id: str,
    text: str,
    notify: bool = True,
) -> None:
    reply_to = MessageRef(channel_id=room_id, message_id=reply_to_event_id)
    await transport.send(
        channel_id=room_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=notify),
    )


async def _send_startup(cfg: MatrixBridgeConfig) -> None:
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


def _resolve_openai_api_key(
    cfg: MatrixVoiceTranscriptionConfig,
) -> str | None:
    env_key = os.environ.get("OPENAI_API_KEY")
    if isinstance(env_key, str):
        env_key = env_key.strip()
        if env_key:
            return env_key
    return None


def _normalize_voice_filename(mxc_url: str, mime_type: str | None) -> str:
    if mime_type == "audio/ogg":
        return "voice.ogg"
    if mime_type == "audio/mp4":
        return "voice.m4a"
    if mime_type == "audio/webm":
        return "voice.webm"
    return "voice.dat"


async def _transcribe_audio_matrix(
    audio_bytes: bytes,
    *,
    filename: str,
    api_key: str,
    model: str,
    mime_type: str | None = None,
    chunking_strategy: str = "auto",
) -> str | None:
    """
    Transcribe audio using OpenAI Whisper API.

    Args:
        audio_bytes: Raw audio file bytes
        filename: Filename for the audio (used by OpenAI to determine format)
        api_key: OpenAI API key
        model: OpenAI transcription model (e.g., gpt-4o-mini-transcribe)
        mime_type: Optional MIME type (unused, kept for compatibility)
        chunking_strategy: Chunking strategy for API (unused, kept for compatibility)

    Returns:
        Transcribed text, or None if transcription failed
    """
    import io

    from openai import AsyncOpenAI, OpenAIError

    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = filename

    async with AsyncOpenAI(api_key=api_key, timeout=120) as client:
        try:
            response = await client.audio.transcriptions.create(
                model=model,
                file=audio_file,
            )
        except OpenAIError as exc:
            logger.error(
                "matrix.transcribe.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    return response.text


async def _transcribe_voice(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
) -> str | None:
    """Transcribe voice message using OpenAI."""
    voice = msg.voice
    if voice is None:
        return msg.text

    settings = cfg.voice_transcription
    if settings is None or not settings.enabled:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="voice transcription is disabled.",
        )
        return None

    api_key = _resolve_openai_api_key(settings)
    if not api_key:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="voice transcription requires OPENAI_API_KEY.",
        )
        return None

    if voice.size is not None and voice.size > _OPENAI_AUDIO_MAX_BYTES:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="voice message is too large to transcribe.",
        )
        return None

    # Get encryption info from raw content (for encrypted voice messages)
    file_info = voice.raw.get("file") if voice.raw else None
    audio_bytes = await cfg.client.download_file(voice.mxc_url, file_info=file_info)
    if not audio_bytes:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="failed to download voice message.",
        )
        return None

    if len(audio_bytes) > _OPENAI_AUDIO_MAX_BYTES:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="voice message is too large to transcribe.",
        )
        return None

    filename = _normalize_voice_filename(voice.mxc_url, voice.mimetype)
    transcript = await _transcribe_audio_matrix(
        audio_bytes,
        filename=filename,
        api_key=api_key,
        model=_OPENAI_TRANSCRIPTION_MODEL,
        chunking_strategy=_OPENAI_TRANSCRIPTION_CHUNKING,
        mime_type=voice.mimetype,
    )

    if transcript is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="voice transcription failed.",
        )
        return None

    transcript = transcript.strip()
    if not transcript:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=msg.room_id,
            reply_to_event_id=msg.event_id,
            text="voice transcription returned empty text.",
        )
        return None

    return transcript


async def _process_file_attachments(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
) -> str:
    """Process file attachments and return text with @FILENAME references."""
    if not msg.attachments:
        return msg.text

    file_cfg = cfg.file_download
    if file_cfg is None or not file_cfg.enabled:
        return msg.text

    download_dir = file_cfg.download_dir or Path.cwd()

    text_refs, errors = await process_attachments(
        cfg.client,
        msg.attachments,
        download_dir,
        max_size=file_cfg.max_size_bytes,
    )

    if errors:
        for error in errors:
            logger.warning("matrix.file.error", error=error)

    if text_refs and msg.text:
        return f"{text_refs}\n\n{msg.text}"
    if text_refs:
        return text_refs
    return msg.text


async def _handle_cancel(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    running_tasks: RunningTasks,
) -> None:
    room_id = msg.room_id
    event_id = msg.event_id
    reply_to = msg.reply_to_event_id

    if reply_to is None:
        if msg.reply_to_text:
            await _send_plain(
                cfg.exec_cfg.transport,
                room_id=room_id,
                reply_to_event_id=event_id,
                text="nothing is currently running for that message.",
            )
            return
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=room_id,
            reply_to_event_id=event_id,
            text="reply to the progress message to cancel.",
        )
        return

    progress_ref = MessageRef(channel_id=room_id, message_id=reply_to)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await _send_plain(
            cfg.exec_cfg.transport,
            room_id=room_id,
            reply_to_event_id=event_id,
            text="nothing is currently running for that message.",
        )
        return

    logger.info(
        "cancel.requested",
        room_id=room_id,
        progress_event_id=reply_to,
    )
    running_task.cancel_requested.set()


async def _handle_cancel_reaction(
    cfg: MatrixBridgeConfig,
    reaction: MatrixReaction,
    running_tasks: RunningTasks,
) -> None:
    """Handle cancel reaction on a progress message."""
    if reaction.key not in _CANCEL_REACTIONS:
        return

    progress_ref = MessageRef(
        channel_id=reaction.room_id,
        message_id=reaction.target_event_id,
    )
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        return

    logger.info(
        "cancel.reaction",
        room_id=reaction.room_id,
        target_event_id=reaction.target_event_id,
        sender=reaction.sender,
    )
    running_task.cancel_requested.set()


async def _wait_for_resume(running_task: RunningTask) -> ResumeToken | None:
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
    resume = await _wait_for_resume(running_task)
    if resume is None:
        await _send_plain(
            cfg.exec_cfg.transport,
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
    try:
        try:
            entry = runtime.resolve_runner(
                resume_token=resume_token,
                engine_override=engine_override,
            )
        except RunnerUnavailableError as exc:
            await _send_plain(
                exec_cfg.transport,
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
                exec_cfg.transport,
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


def _split_command_args(text: str) -> tuple[str, ...]:
    if not text.strip():
        return ()
    try:
        return tuple(shlex.split(text))
    except ValueError:
        return tuple(text.split())


class _CaptureTransport:
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
    def __init__(
        self,
        *,
        exec_cfg: ExecBridgeConfig,
        runtime: TransportRuntime,
        running_tasks: RunningTasks,
        scheduler: ThreadScheduler,
        room_id: str,
        event_id: str,
    ) -> None:
        self._exec_cfg = exec_cfg
        self._runtime = runtime
        self._running_tasks = running_tasks
        self._scheduler = scheduler
        self._room_id = room_id
        self._event_id = event_id
        self._reply_ref = MessageRef(channel_id=room_id, message_id=event_id)

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
            await _run_engine(
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
        await _run_engine(
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
) -> None:
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
    from dataclasses import replace

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


async def _sync_loop(
    cfg: MatrixBridgeConfig,
    message_queue: anyio.abc.ObjectSendStream[MatrixIncomingMessage],  # type: ignore[name-defined]
    reaction_queue: anyio.abc.ObjectSendStream[MatrixReaction],  # type: ignore[name-defined]
) -> None:
    """Continuous sync loop with reconnection."""
    backoff = ExponentialBackoff()
    allowed_room_ids = _allowed_room_ids(
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
    await cfg.client.sync(timeout_ms=10000)

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

    try:
        if not await _startup_sequence(cfg):
            return

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
        ](max_buffer_size=100)
        reaction_send, reaction_recv = anyio.create_memory_object_stream[
            MatrixReaction
        ](max_buffer_size=100)

        async with anyio.create_task_group() as tg:

            async def run_job(
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

                if _is_cancel_command(text):
                    tg.start_soon(_handle_cancel, cfg, msg, running_tasks)
                    continue

                command_id, args_text = _parse_slash_command(text)
                if command_id is not None and command_id not in reserved_commands:
                    if command_id not in command_ids:
                        command_ids.update(
                            cid.lower() for cid in list_command_ids(allowlist=allowlist)
                        )
                    if command_id in command_ids:
                        tg.start_soon(
                            _dispatch_command,
                            cfg,
                            msg,
                            text,
                            command_id,
                            args_text,
                            running_tasks,
                            scheduler,
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
                        cfg.exec_cfg.transport,
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
                        room_id,  # type: ignore[arg-type]  # str, but expects int
                        event_id,  # type: ignore[arg-type]  # str, but expects int
                        text,
                        resume_token,
                        context,
                    )

    finally:
        await cfg.exec_cfg.transport.close()
