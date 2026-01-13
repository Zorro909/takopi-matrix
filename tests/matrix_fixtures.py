"""Shared fixtures and utilities for Matrix transport tests."""

from __future__ import annotations

from typing import Any

from takopi_matrix.types import (
    MatrixFile,
    MatrixIncomingMessage,
    MatrixReaction,
    MatrixVoice,
)

MATRIX_ROOM_ID = "!testroom:example.org"
MATRIX_USER_ID = "@bot:example.org"
MATRIX_SENDER = "@user:example.org"
MATRIX_EVENT_ID = "$evt123:example.org"


def make_matrix_message(
    text: str = "test message",
    room_id: str = MATRIX_ROOM_ID,
    event_id: str = MATRIX_EVENT_ID,
    sender: str = MATRIX_SENDER,
    **kwargs: Any,
) -> MatrixIncomingMessage:
    """Create a MatrixIncomingMessage with sensible defaults."""
    return MatrixIncomingMessage(
        transport="matrix",
        room_id=room_id,
        event_id=event_id,
        sender=sender,
        text=text,
        **kwargs,
    )


def make_matrix_file(
    mxc_url: str = "mxc://example.org/abc123",
    filename: str = "test.txt",
    mimetype: str = "text/plain",
    size: int | None = 1024,
    file_info: dict[str, Any] | None = None,
) -> MatrixFile:
    """Create a MatrixFile with sensible defaults."""
    return MatrixFile(
        mxc_url=mxc_url,
        filename=filename,
        mimetype=mimetype,
        size=size,
        file_info=file_info,
    )


def make_matrix_voice(
    mxc_url: str = "mxc://example.org/voice123",
    mimetype: str | None = "audio/ogg",
    size: int | None = 5000,
    duration_ms: int | None = 3000,
    raw: dict[str, Any] | None = None,
) -> MatrixVoice:
    """Create a MatrixVoice with sensible defaults."""
    return MatrixVoice(
        mxc_url=mxc_url,
        mimetype=mimetype,
        size=size,
        duration_ms=duration_ms,
        raw=raw or {"content": {}},
    )


def make_matrix_reaction(
    target_event_id: str = MATRIX_EVENT_ID,
    key: str = "👍",
    room_id: str = MATRIX_ROOM_ID,
    event_id: str = "$reaction456:example.org",
    sender: str = MATRIX_SENDER,
) -> MatrixReaction:
    """Create a MatrixReaction with sensible defaults."""
    return MatrixReaction(
        room_id=room_id,
        event_id=event_id,
        target_event_id=target_event_id,
        sender=sender,
        key=key,
    )


class FakeMatrixClient:
    """Fake MatrixClient for testing file operations."""

    def __init__(self) -> None:
        self.download_responses: dict[str, bytes | Exception] = {}
        self.download_calls: list[tuple[str, int | None, dict[str, Any] | None]] = []

    async def download_file(
        self,
        mxc_url: str,
        *,
        max_size: int | None = None,
        file_info: dict[str, Any] | None = None,
    ) -> bytes | None:
        """Return configured response for the given mxc_url."""
        self.download_calls.append((mxc_url, max_size, file_info))
        response = self.download_responses.get(mxc_url)
        if isinstance(response, Exception):
            raise response
        return response
