from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MatrixFile:
    """Represents an attached file/media in a Matrix message."""

    mxc_url: str
    filename: str
    mimetype: str | None = None
    size: int | None = None
    file_info: dict[str, Any] | None = None  # Encryption info for E2EE files


@dataclass(frozen=True, slots=True)
class MatrixVoice:
    """Represents a voice message attachment."""

    mxc_url: str
    mimetype: str | None
    size: int | None
    duration_ms: int | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MatrixIncomingMessage:
    """Represents an incoming message from Matrix."""

    transport: str
    room_id: str
    event_id: str
    sender: str
    text: str
    thread_root_event_id: str | None = None
    reply_to_event_id: str | None = None
    reply_to_text: str | None = None
    reply_to_text_fetch_failed: bool = False
    formatted_body: str | None = None
    attachments: list[MatrixFile] | None = None
    voice: MatrixVoice | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MatrixReaction:
    """Represents a reaction event from Matrix."""

    room_id: str
    event_id: str
    target_event_id: str
    sender: str
    key: str
