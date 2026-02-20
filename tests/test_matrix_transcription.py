"""Tests for bridge/transcription.py - Voice transcription utilities."""

from __future__ import annotations

import pytest

from takopi_matrix.bridge.transcription import (
    _normalize_voice_filename,
    _resolve_openai_api_key,
    _OPENAI_AUDIO_MAX_BYTES,
)
from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig


# --- _normalize_voice_filename tests ---


def test_normalize_voice_filename_ogg() -> None:
    """OGG MIME type produces .ogg extension."""
    result = _normalize_voice_filename("mxc://example.org/abc", "audio/ogg")
    assert result == "voice.ogg"


def test_normalize_voice_filename_mp4() -> None:
    """MP4 MIME type produces .m4a extension."""
    result = _normalize_voice_filename("mxc://example.org/abc", "audio/mp4")
    assert result == "voice.m4a"


def test_normalize_voice_filename_webm() -> None:
    """WebM MIME type produces .webm extension."""
    result = _normalize_voice_filename("mxc://example.org/abc", "audio/webm")
    assert result == "voice.webm"


def test_normalize_voice_filename_unknown() -> None:
    """Unknown MIME type produces .dat extension."""
    result = _normalize_voice_filename("mxc://example.org/abc", "audio/unknown")
    assert result == "voice.dat"


def test_normalize_voice_filename_none() -> None:
    """None MIME type produces .dat extension."""
    result = _normalize_voice_filename("mxc://example.org/abc", None)
    assert result == "voice.dat"


# --- _resolve_openai_api_key tests ---


def test_resolve_openai_api_key_from_env(monkeypatch) -> None:
    """Resolves API key from environment."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    cfg = MatrixVoiceTranscriptionConfig(enabled=True)
    result = _resolve_openai_api_key(cfg)
    assert result == "sk-test-key"


def test_resolve_openai_api_key_strips_whitespace(monkeypatch) -> None:
    """API key is stripped of whitespace."""
    monkeypatch.setenv("OPENAI_API_KEY", "  sk-test-key  ")
    cfg = MatrixVoiceTranscriptionConfig(enabled=True)
    result = _resolve_openai_api_key(cfg)
    assert result == "sk-test-key"


def test_resolve_openai_api_key_empty(monkeypatch) -> None:
    """Empty API key returns None."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    cfg = MatrixVoiceTranscriptionConfig(enabled=True)
    result = _resolve_openai_api_key(cfg)
    assert result is None


def test_resolve_openai_api_key_whitespace_only(monkeypatch) -> None:
    """Whitespace-only API key returns None."""
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    cfg = MatrixVoiceTranscriptionConfig(enabled=True)
    result = _resolve_openai_api_key(cfg)
    assert result is None


def test_resolve_openai_api_key_not_set(monkeypatch) -> None:
    """Missing API key returns None."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = MatrixVoiceTranscriptionConfig(enabled=True)
    result = _resolve_openai_api_key(cfg)
    assert result is None


# --- Constants tests ---


def test_openai_audio_max_bytes() -> None:
    """Max bytes constant is 25MB."""
    assert _OPENAI_AUDIO_MAX_BYTES == 25 * 1024 * 1024


# --- _send_plain tests ---


class FakeTransport:
    """Fake transport for testing."""

    def __init__(self) -> None:
        self.send_calls: list[dict] = []

    async def send(self, *, channel_id, message, options=None):
        from takopi.api import MessageRef

        self.send_calls.append(
            {
                "channel_id": channel_id,
                "message": message,
                "options": options,
            }
        )
        return MessageRef(channel_id=channel_id, message_id="$sent1")


@pytest.mark.anyio
async def test_send_plain_basic() -> None:
    """_send_plain sends a message."""
    from takopi_matrix.bridge.transcription import _send_plain

    transport = FakeTransport()
    await _send_plain(
        transport,  # type: ignore
        room_id="!room:example.org",
        reply_to_event_id="$event123",
        text="Hello",
    )

    assert len(transport.send_calls) == 1
    assert transport.send_calls[0]["channel_id"] == "!room:example.org"
    assert transport.send_calls[0]["message"].text == "Hello"
    assert transport.send_calls[0]["options"].reply_to.message_id == "$event123"
    assert transport.send_calls[0]["options"].notify is True


@pytest.mark.anyio
async def test_send_plain_silent() -> None:
    """_send_plain respects notify=False."""
    from takopi_matrix.bridge.transcription import _send_plain

    transport = FakeTransport()
    await _send_plain(
        transport,  # type: ignore
        room_id="!room:example.org",
        reply_to_event_id="$event123",
        text="Silent",
        notify=False,
    )

    assert len(transport.send_calls) == 1
    assert transport.send_calls[0]["options"].notify is False


# --- _process_file_attachments tests ---


@pytest.mark.anyio
async def test_process_file_attachments_no_attachments() -> None:
    """Returns text unchanged when no attachments."""
    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.types import MatrixIncomingMessage

    class FakeConfig:
        file_download = None

    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello world",
    )

    result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore
    assert result == "Hello world"


@pytest.mark.anyio
async def test_process_file_attachments_disabled() -> None:
    """Returns text unchanged when file download disabled."""
    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.bridge.config import MatrixFileDownloadConfig
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeConfig:
        file_download = MatrixFileDownloadConfig(
            enabled=False, max_size_bytes=50 * 1024 * 1024
        )

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello world",
        attachments=[attachment],
    )

    result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore
    assert result == "Hello world"


@pytest.mark.anyio
async def test_process_file_attachments_none_config() -> None:
    """Returns text unchanged when file_download is None."""
    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeConfig:
        file_download = None

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello world",
        attachments=[attachment],
    )

    result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore
    assert result == "Hello world"


# --- _transcribe_voice tests ---


@pytest.mark.anyio
async def test_transcribe_voice_no_voice() -> None:
    """Returns original text when no voice message."""
    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.types import MatrixIncomingMessage

    class FakeConfig:
        voice_transcription = None

    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello world",
        voice=None,
    )

    result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore
    assert result == "Hello world"


@pytest.mark.anyio
async def test_transcribe_voice_disabled() -> None:
    """Sends message when transcription disabled."""
    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=False)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=None,
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore
    assert result is None
    assert len(transport.send_calls) == 1
    assert "disabled" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_no_api_key(monkeypatch) -> None:
    """Sends message when no API key."""
    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=None,
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore
    assert result is None
    assert len(transport.send_calls) == 1
    assert "OPENAI_API_KEY" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_too_large() -> None:
    """Sends message when voice file too large."""
    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import (
        _transcribe_voice,
        _OPENAI_AUDIO_MAX_BYTES,
    )
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=_OPENAI_AUDIO_MAX_BYTES + 1,  # Too large
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore
    assert result is None
    assert len(transport.send_calls) == 1
    assert "too large" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_download_fails(monkeypatch) -> None:
    """Sends message when file download fails."""
    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeClient:
        async def download_file(self, mxc_url, file_info=None) -> bytes | None:
            return None  # Download failed

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )
        client = FakeClient()

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=1000,  # Small enough
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore
    assert result is None
    assert len(transport.send_calls) == 1
    assert "failed to download" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_downloaded_file_too_large(monkeypatch) -> None:
    """Sends message when downloaded file is too large."""
    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import (
        _transcribe_voice,
        _OPENAI_AUDIO_MAX_BYTES,
    )
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeClient:
        async def download_file(self, mxc_url, file_info=None) -> bytes | None:
            # Return a large byte array
            return b"x" * (_OPENAI_AUDIO_MAX_BYTES + 1)

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )
        client = FakeClient()

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=None,  # Unknown size
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore
    assert result is None
    assert len(transport.send_calls) == 1
    assert "too large" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_transcription_fails(monkeypatch) -> None:
    """Sends message when transcription fails."""
    from unittest.mock import AsyncMock, patch

    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeClient:
        async def download_file(self, mxc_url, file_info=None) -> bytes | None:
            return b"audio data"

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )
        client = FakeClient()

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=1000,
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    # Mock _transcribe_audio_matrix to return None
    with patch(
        "takopi_matrix.bridge.transcription._transcribe_audio_matrix",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore

    assert result is None
    assert len(transport.send_calls) == 1
    assert "transcription failed" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_empty_transcript(monkeypatch) -> None:
    """Sends message when transcript is empty."""
    from unittest.mock import AsyncMock, patch

    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeClient:
        async def download_file(self, mxc_url, file_info=None) -> bytes | None:
            return b"audio data"

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )
        client = FakeClient()

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=1000,
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    # Mock _transcribe_audio_matrix to return empty string
    with patch(
        "takopi_matrix.bridge.transcription._transcribe_audio_matrix",
        new_callable=AsyncMock,
        return_value="   ",  # Whitespace-only
    ):
        result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore

    assert result is None
    assert len(transport.send_calls) == 1
    assert "empty text" in transport.send_calls[0]["message"].text


@pytest.mark.anyio
async def test_transcribe_voice_success(monkeypatch) -> None:
    """Returns transcript on success."""
    from unittest.mock import AsyncMock, patch

    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = FakeTransport()

    class FakePresenter:
        pass

    class FakeClient:
        async def download_file(self, mxc_url, file_info=None) -> bytes | None:
            return b"audio data"

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )
        client = FakeClient()

    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=1000,
        raw={},
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    # Mock _transcribe_audio_matrix to return transcript
    with patch(
        "takopi_matrix.bridge.transcription._transcribe_audio_matrix",
        new_callable=AsyncMock,
        return_value="  Hello, this is a transcribed message.  ",
    ):
        result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore

    assert result == "Hello, this is a transcribed message."
    assert len(transport.send_calls) == 0  # No error messages sent


@pytest.mark.anyio
async def test_transcribe_voice_with_file_info(monkeypatch) -> None:
    """Passes file info for encrypted messages."""
    from unittest.mock import AsyncMock, patch

    from takopi.api import ExecBridgeConfig

    from takopi_matrix.bridge.transcription import _transcribe_voice
    from takopi_matrix.bridge.config import MatrixVoiceTranscriptionConfig
    from takopi_matrix.types import MatrixIncomingMessage, MatrixVoice

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = FakeTransport()
    download_calls = []

    class FakePresenter:
        pass

    class FakeClient:
        async def download_file(self, mxc_url, file_info=None) -> bytes | None:
            download_calls.append({"mxc_url": mxc_url, "file_info": file_info})
            return b"audio data"

    class FakeConfig:
        voice_transcription = MatrixVoiceTranscriptionConfig(enabled=True)
        exec_cfg = ExecBridgeConfig(
            transport=transport,  # type: ignore
            presenter=FakePresenter(),  # type: ignore
            final_notify=True,
        )
        client = FakeClient()

    file_info = {"url": "mxc://example.org/encrypted", "key": {"k": "abc123"}}
    voice = MatrixVoice(
        mxc_url="mxc://example.org/voice123",
        mimetype="audio/ogg",
        duration_ms=5000,
        size=1000,
        raw={"file": file_info},  # Encrypted file info
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",
        voice=voice,
    )

    with patch(
        "takopi_matrix.bridge.transcription._transcribe_audio_matrix",
        new_callable=AsyncMock,
        return_value="Transcript",
    ):
        result = await _transcribe_voice(FakeConfig(), msg)  # type: ignore

    assert result == "Transcript"
    assert len(download_calls) == 1
    assert download_calls[0]["file_info"] == file_info


# --- _process_file_attachments additional tests ---


@pytest.mark.anyio
async def test_process_file_attachments_enabled_with_refs() -> None:
    """Returns file refs combined with text when enabled."""
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.bridge.config import MatrixFileDownloadConfig
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeClient:
        pass

    class FakeConfig:
        file_download = MatrixFileDownloadConfig(
            enabled=True, max_size_bytes=50 * 1024 * 1024, download_dir=Path("/tmp")
        )
        client = FakeClient()

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello world",
        attachments=[attachment],
    )

    with patch(
        "takopi_matrix.files.process_attachments",
        new_callable=AsyncMock,
        return_value=("@file.txt", []),
    ):
        result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore

    assert result == "@file.txt\n\nHello world"


@pytest.mark.anyio
async def test_process_file_attachments_refs_only() -> None:
    """Returns only file refs when no text."""
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.bridge.config import MatrixFileDownloadConfig
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeClient:
        pass

    class FakeConfig:
        file_download = MatrixFileDownloadConfig(
            enabled=True, max_size_bytes=50 * 1024 * 1024, download_dir=Path("/tmp")
        )
        client = FakeClient()

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="",  # No text
        attachments=[attachment],
    )

    with patch(
        "takopi_matrix.files.process_attachments",
        new_callable=AsyncMock,
        return_value=("@file.txt", []),
    ):
        result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore

    assert result == "@file.txt"


@pytest.mark.anyio
async def test_process_file_attachments_no_refs() -> None:
    """Returns original text when no refs generated."""
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.bridge.config import MatrixFileDownloadConfig
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeClient:
        pass

    class FakeConfig:
        file_download = MatrixFileDownloadConfig(
            enabled=True, max_size_bytes=50 * 1024 * 1024, download_dir=Path("/tmp")
        )
        client = FakeClient()

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello world",
        attachments=[attachment],
    )

    with patch(
        "takopi_matrix.files.process_attachments",
        new_callable=AsyncMock,
        return_value=("", []),  # No refs generated
    ):
        result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore

    assert result == "Hello world"


@pytest.mark.anyio
async def test_process_file_attachments_with_errors() -> None:
    """Logs errors when file processing fails."""
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.bridge.config import MatrixFileDownloadConfig
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeClient:
        pass

    class FakeConfig:
        file_download = MatrixFileDownloadConfig(
            enabled=True, max_size_bytes=50 * 1024 * 1024, download_dir=Path("/tmp")
        )
        client = FakeClient()

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello",
        attachments=[attachment],
    )

    with patch(
        "takopi_matrix.files.process_attachments",
        new_callable=AsyncMock,
        return_value=("@file.txt", ["Download failed", "Other error"]),
    ):
        result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore

    # Should still return result even with errors
    assert result == "@file.txt\n\nHello"


@pytest.mark.anyio
async def test_process_file_attachments_default_download_dir() -> None:
    """Uses cwd when no download_dir specified."""
    from pathlib import Path
    from unittest.mock import patch

    from takopi_matrix.bridge.transcription import _process_file_attachments
    from takopi_matrix.bridge.config import MatrixFileDownloadConfig
    from takopi_matrix.types import MatrixFile, MatrixIncomingMessage

    class FakeClient:
        pass

    class FakeConfig:
        file_download = MatrixFileDownloadConfig(
            enabled=True, max_size_bytes=50 * 1024 * 1024, download_dir=None
        )
        client = FakeClient()

    attachment = MatrixFile(
        filename="file.txt",
        mxc_url="mxc://example.org/abc",
        mimetype="text/plain",
        size=100,
    )
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$event123",
        sender="@user:example.org",
        text="Hello",
        attachments=[attachment],
    )

    captured_dir = None

    async def mock_process_attachments(client, attachments, download_dir, max_size):
        nonlocal captured_dir
        captured_dir = download_dir
        return ("@file.txt", [])

    with patch(
        "takopi_matrix.files.process_attachments",
        side_effect=mock_process_attachments,
    ):
        result = await _process_file_attachments(FakeConfig(), msg)  # type: ignore

    # Should use cwd as default
    assert captured_dir == Path.cwd()
    assert result == "@file.txt\n\nHello"
