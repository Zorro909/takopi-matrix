"""Voice transcription using OpenAI Whisper."""

from __future__ import annotations

import os
from pathlib import Path

from takopi.api import MessageRef, RenderedMessage, SendOptions, Transport
from takopi.logging import get_logger

from ..types import MatrixIncomingMessage
from .config import MatrixBridgeConfig, MatrixVoiceTranscriptionConfig

logger = get_logger(__name__)

_OPENAI_AUDIO_MAX_BYTES = 25 * 1024 * 1024
_OPENAI_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
_OPENAI_TRANSCRIPTION_CHUNKING = "auto"


def _resolve_openai_api_key(
    cfg: MatrixVoiceTranscriptionConfig,
) -> str | None:
    """Resolve OpenAI API key from environment."""
    env_key = os.environ.get("OPENAI_API_KEY")
    if isinstance(env_key, str):
        env_key = env_key.strip()
        if env_key:
            return env_key
    return None


def _normalize_voice_filename(mxc_url: str, mime_type: str | None) -> str:
    """Normalize voice filename based on MIME type."""
    if mime_type == "audio/ogg":
        return "voice.ogg"
    if mime_type == "audio/mp4":
        return "voice.m4a"
    if mime_type == "audio/webm":
        return "voice.webm"
    return "voice.dat"


async def _send_plain(
    transport: Transport,
    *,
    room_id: str,
    reply_to_event_id: str,
    text: str,
    notify: bool = True,
) -> None:
    """Send a plain text message as a reply."""
    reply_to = MessageRef(channel_id=room_id, message_id=reply_to_event_id)
    await transport.send(
        channel_id=room_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=notify),
    )


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

    from ..files import process_attachments

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
