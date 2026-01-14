"""Matrix event parsers for converting nio events to domain types."""

from __future__ import annotations

from typing import Any

from ..types import MatrixFile, MatrixIncomingMessage, MatrixReaction, MatrixVoice


def parse_matrix_error(response: dict[str, Any]) -> tuple[str, float | None]:
    """Parse Matrix error response for errcode and retry_after."""
    errcode = response.get("errcode", "")
    retry_after_ms = response.get("retry_after_ms")
    retry_after = retry_after_ms / 1000.0 if retry_after_ms else None
    return errcode, retry_after


def _extract_reply_to(content: dict[str, Any]) -> str | None:
    """Extract the event ID being replied to from m.relates_to."""
    relates_to = content.get("m.relates_to")
    if not isinstance(relates_to, dict):
        return None
    in_reply_to = relates_to.get("m.in_reply_to")
    if not isinstance(in_reply_to, dict):
        return None
    event_id = in_reply_to.get("event_id")
    return event_id if isinstance(event_id, str) else None


def _parse_event_common(
    event: Any,
    room_id: str,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
) -> dict[str, Any] | None:
    """
    Extract fields common to all Matrix event types.

    Performs validation and extracts:
    - sender
    - event_id
    - source dict
    - content dict
    - reply_to_event_id

    Returns None if validation fails (wrong room, missing fields, own message).
    """
    # Validate room
    if room_id not in allowed_room_ids:
        return None

    # Extract and validate basic fields
    sender = getattr(event, "sender", None)
    event_id = getattr(event, "event_id", None)

    if sender is None or event_id is None:
        return None

    # Filter out own messages
    if sender == own_user_id:
        return None

    # Extract source and content
    source = getattr(event, "source", {})
    content = source.get("content", {}) if isinstance(source, dict) else {}

    # Extract reply information
    reply_to_event_id = _extract_reply_to(content)

    return {
        "sender": sender,
        "event_id": event_id,
        "source": source if isinstance(source, dict) else None,
        "content": content,
        "reply_to_event_id": reply_to_event_id,
    }


def _extract_mxc_url(event: Any, content: dict[str, Any]) -> str | None:
    """
    Extract mxc URL from regular or encrypted media/audio event.

    Tries two sources:
    1. Regular media: event.url attribute
    2. Encrypted media: content["file"]["url"]

    Returns None if no URL found.
    """
    # Try regular media URL
    url = getattr(event, "url", None)
    if url:
        return url

    # Try encrypted media structure
    file_info = content.get("file", {})
    if isinstance(file_info, dict):
        url = file_info.get("url")
        if url:
            return url

    return None


def parse_room_message(
    event: Any,
    room_id: str,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
) -> MatrixIncomingMessage | None:
    """Parse a nio RoomMessageText event into MatrixIncomingMessage."""
    common = _parse_event_common(
        event, room_id, allowed_room_ids=allowed_room_ids, own_user_id=own_user_id
    )
    if not common:
        return None

    # Extract message-specific fields
    body = getattr(event, "body", "")
    formatted_body = getattr(event, "formatted_body", None)

    return MatrixIncomingMessage(
        transport="matrix",
        room_id=room_id,
        event_id=common["event_id"],
        sender=common["sender"],
        text=body,
        reply_to_event_id=common["reply_to_event_id"],
        reply_to_text=None,
        formatted_body=formatted_body,
        raw=common["source"],
    )


def parse_room_media(
    event: Any,
    room_id: str,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
) -> MatrixIncomingMessage | None:
    """Parse a nio media event into MatrixIncomingMessage with attachments."""
    common = _parse_event_common(
        event, room_id, allowed_room_ids=allowed_room_ids, own_user_id=own_user_id
    )
    if not common:
        return None

    # Extract media-specific fields
    body = getattr(event, "body", "")

    # Extract MXC URL (handles both regular and encrypted media)
    url = _extract_mxc_url(event, common["content"])
    if not url:
        return None

    info = common["content"].get("info", {})
    mimetype = info.get("mimetype") if isinstance(info, dict) else None
    size = info.get("size") if isinstance(info, dict) else None

    # Get encryption info for encrypted files
    file_encryption_info = common["content"].get("file")
    if not isinstance(file_encryption_info, dict):
        file_encryption_info = None

    attachment = MatrixFile(
        mxc_url=url,
        filename=body,
        mimetype=mimetype,
        size=size,
        file_info=file_encryption_info,
    )

    return MatrixIncomingMessage(
        transport="matrix",
        room_id=room_id,
        event_id=common["event_id"],
        sender=common["sender"],
        text="",
        reply_to_event_id=common["reply_to_event_id"],
        reply_to_text=None,
        attachments=[attachment],
        raw=common["source"],
    )


def parse_room_audio(
    event: Any,
    room_id: str,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
) -> MatrixIncomingMessage | None:
    """Parse a nio audio event into MatrixIncomingMessage with voice."""
    common = _parse_event_common(
        event, room_id, allowed_room_ids=allowed_room_ids, own_user_id=own_user_id
    )
    if not common:
        return None

    # Extract audio-specific fields
    # Extract MXC URL (handles both regular and encrypted audio)
    url = _extract_mxc_url(event, common["content"])
    if not url:
        return None

    info = common["content"].get("info", {})
    mimetype = info.get("mimetype") if isinstance(info, dict) else None
    size = info.get("size") if isinstance(info, dict) else None
    duration = info.get("duration") if isinstance(info, dict) else None

    voice = MatrixVoice(
        mxc_url=url,
        mimetype=mimetype,
        size=size,
        duration_ms=duration,
        raw=common["content"],
    )

    return MatrixIncomingMessage(
        transport="matrix",
        room_id=room_id,
        event_id=common["event_id"],
        sender=common["sender"],
        text="",
        reply_to_event_id=common["reply_to_event_id"],
        reply_to_text=None,
        voice=voice,
        raw=common["source"],
    )


def parse_reaction(
    event: Any,
    room_id: str,
    *,
    allowed_room_ids: set[str],
    own_user_id: str,
) -> MatrixReaction | None:
    """Parse a nio reaction event."""
    if room_id not in allowed_room_ids:
        return None

    sender = getattr(event, "sender", None)
    event_id = getattr(event, "event_id", None)

    # Type narrowing: ensure sender and event_id are not None
    if sender is None or event_id is None:
        return None
    if sender == own_user_id:
        return None

    source = getattr(event, "source", {})
    content = source.get("content", {}) if isinstance(source, dict) else {}

    relates_to = content.get("m.relates_to", {})
    if not isinstance(relates_to, dict):
        return None

    rel_type = relates_to.get("rel_type")
    if rel_type != "m.annotation":
        return None

    target_event_id = relates_to.get("event_id")
    key = relates_to.get("key")

    if not target_event_id or not key:
        return None

    return MatrixReaction(
        room_id=room_id,
        event_id=event_id,
        target_event_id=target_event_id,
        sender=sender,
        key=key,
    )
