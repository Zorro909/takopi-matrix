"""Tests for Matrix client and outbox pattern."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
import pytest

from takopi_matrix.client import (
    DELETE_PRIORITY,
    EDIT_PRIORITY,
    SEND_PRIORITY,
    TYPING_PRIORITY,
    MatrixOutbox,
    MatrixRetryAfter,
    OutboxOp,
    RetryAfter,
    parse_matrix_error,
    parse_reaction,
    parse_room_audio,
    parse_room_media,
    parse_room_message,
)
from matrix_fixtures import MATRIX_ROOM_ID, MATRIX_SENDER, MATRIX_USER_ID


# --- RetryAfter exceptions ---


def test_retry_after_exception() -> None:
    """RetryAfter stores retry_after value."""
    exc = RetryAfter(5.0, "rate limited")
    assert exc.retry_after == 5.0
    assert exc.description == "rate limited"


def test_matrix_retry_after_exception() -> None:
    """MatrixRetryAfter is a subclass of RetryAfter."""
    exc = MatrixRetryAfter(3.0)
    assert isinstance(exc, RetryAfter)
    assert exc.retry_after == 3.0


# --- Outbox tests ---


@dataclass
class FakeClock:
    """Fake clock for testing."""

    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


def _make_outbox(
    clock: FakeClock | None = None,
    on_error: Any = None,
) -> MatrixOutbox:
    """Create outbox with fake clock."""
    clock = clock or FakeClock()
    return MatrixOutbox(
        interval=0.1,
        clock=clock,
        on_error=on_error,
    )


@pytest.mark.anyio
async def test_outbox_enqueue_single() -> None:
    """Single operation can be enqueued and executed."""
    executed = []

    async def op():
        executed.append(1)
        return "result"

    outbox = _make_outbox()
    try:
        outbox_op = OutboxOp(
            execute=op,
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        result = await outbox.enqueue(key="test", op=outbox_op)
        assert result == "result"
        assert executed == [1]
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_op_done_event() -> None:
    """OutboxOp done event is set when result is set."""
    op = OutboxOp(
        execute=lambda: anyio.sleep(0),
        priority=SEND_PRIORITY,
        queued_at=0,
        updated_at=0,
        room_id=MATRIX_ROOM_ID,
    )
    assert not op.done.is_set()
    op.set_result("result")
    assert op.done.is_set()
    assert op.result == "result"


def test_outbox_op_set_result_idempotent() -> None:
    """set_result only works once."""
    op = OutboxOp(
        execute=lambda: None,
        priority=SEND_PRIORITY,
        queued_at=0,
        updated_at=0,
        room_id=MATRIX_ROOM_ID,
    )
    op.set_result("first")
    op.set_result("second")
    # First result wins
    assert op.result == "first"


def test_outbox_priority_constants() -> None:
    """Priority constants are ordered correctly."""
    # Lower values = higher priority (execute first)
    assert SEND_PRIORITY < DELETE_PRIORITY
    assert DELETE_PRIORITY < EDIT_PRIORITY
    assert EDIT_PRIORITY < TYPING_PRIORITY


@pytest.mark.anyio
async def test_outbox_close_is_idempotent() -> None:
    """Closing outbox multiple times is safe."""
    outbox = _make_outbox()
    await outbox.close()
    await outbox.close()  # Should not raise


@pytest.mark.anyio
async def test_outbox_retry_after_delay() -> None:
    """Outbox respects retry_after from exception."""
    attempts = []

    async def failing_then_success():
        attempts.append(time.monotonic())
        if len(attempts) == 1:
            raise MatrixRetryAfter(0.1)  # Short delay
        return "success"

    outbox = MatrixOutbox(interval=0.01)
    try:
        op = OutboxOp(
            execute=failing_then_success,
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        result = await outbox.enqueue(key="retry", op=op)
        assert result == "success"
        assert len(attempts) == 2
    finally:
        await outbox.close()


# --- Error parsing ---


def test_parse_matrix_error_with_retry() -> None:
    """parse_matrix_error extracts retry_after_ms."""
    response = {
        "errcode": "M_LIMIT_EXCEEDED",
        "error": "Too many requests",
        "retry_after_ms": 5000,
    }
    errcode, retry_s = parse_matrix_error(response)
    assert errcode == "M_LIMIT_EXCEEDED"
    assert retry_s == 5.0  # Converted to seconds


def test_parse_matrix_error_without_retry() -> None:
    """parse_matrix_error handles missing retry_after_ms."""
    response = {
        "errcode": "M_FORBIDDEN",
        "error": "Not allowed",
    }
    errcode, retry_s = parse_matrix_error(response)
    assert errcode == "M_FORBIDDEN"
    assert retry_s is None


def test_parse_matrix_error_empty() -> None:
    """parse_matrix_error handles empty dict."""
    errcode, retry_s = parse_matrix_error({})
    assert errcode == ""
    assert retry_s is None


# --- Message parsing ---


class FakeRoomMessageText:
    """Fake nio RoomMessageText event."""

    def __init__(
        self,
        body: str = "test message",
        sender: str = MATRIX_SENDER,
        event_id: str = "$evt:example.org",
        formatted_body: str | None = None,
        source: dict | None = None,
    ):
        self.body = body
        self.sender = sender
        self.event_id = event_id
        self.formatted_body = formatted_body
        self.source = source or {"content": {}}


class FakeRoomMessageImage:
    """Fake nio RoomMessageImage event."""

    def __init__(
        self,
        body: str = "image.png",
        sender: str = MATRIX_SENDER,
        event_id: str = "$evt:example.org",
        url: str = "mxc://example.org/img123",
        mimetype: str = "image/png",
    ):
        self.body = body
        self.sender = sender
        self.event_id = event_id
        self.url = url
        self.mimetype = mimetype
        self.source = {"content": {"info": {"size": 1024, "mimetype": mimetype}}}


class FakeRoomMessageFile:
    """Fake nio RoomMessageFile event."""

    def __init__(
        self,
        body: str = "doc.pdf",
        sender: str = MATRIX_SENDER,
        event_id: str = "$evt:example.org",
        url: str = "mxc://example.org/file123",
        mimetype: str = "application/pdf",
    ):
        self.body = body
        self.sender = sender
        self.event_id = event_id
        self.url = url
        self.mimetype = mimetype
        self.source = {"content": {"info": {"size": 2048, "mimetype": mimetype}}}


class FakeRoomMessageAudio:
    """Fake nio RoomMessageAudio event."""

    def __init__(
        self,
        body: str = "voice.ogg",
        sender: str = MATRIX_SENDER,
        event_id: str = "$evt:example.org",
        url: str = "mxc://example.org/audio123",
        mimetype: str = "audio/ogg",
        duration: int | None = 3000,
    ):
        self.body = body
        self.sender = sender
        self.event_id = event_id
        self.url = url
        self.mimetype = mimetype
        self.duration = duration
        self.source = {
            "content": {
                "info": {"size": 5000, "duration": duration, "mimetype": mimetype}
            }
        }


class FakeReactionEvent:
    """Fake nio ReactionEvent."""

    def __init__(
        self,
        reacts_to: str = "$target:example.org",
        key: str = "👍",
        sender: str = MATRIX_SENDER,
        event_id: str = "$reaction:example.org",
    ):
        self.reacts_to = reacts_to
        self.key = key
        self.sender = sender
        self.event_id = event_id
        self.source = {
            "content": {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": reacts_to,
                    "key": key,
                }
            }
        }


def test_parse_room_message_basic() -> None:
    """Basic text message parsing."""
    event = FakeRoomMessageText(body="hello world")

    result = parse_room_message(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.text == "hello world"
    assert result.room_id == MATRIX_ROOM_ID
    assert result.sender == MATRIX_SENDER


def test_parse_room_message_filters_own() -> None:
    """Own messages are filtered out."""
    event = FakeRoomMessageText(sender=MATRIX_USER_ID)

    result = parse_room_message(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_message_filters_room() -> None:
    """Messages from non-allowed rooms are filtered."""
    event = FakeRoomMessageText()

    result = parse_room_message(
        event=event,
        room_id="!other:example.org",
        allowed_room_ids={MATRIX_ROOM_ID},  # Only allow one room
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_message_with_formatted_body() -> None:
    """Formatted body is captured."""
    event = FakeRoomMessageText(
        body="hello",
        formatted_body="<p>hello</p>",
    )

    result = parse_room_message(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.formatted_body == "<p>hello</p>"


def test_parse_room_message_with_reply() -> None:
    """Reply metadata is extracted."""
    event = FakeRoomMessageText(
        source={
            "content": {
                "m.relates_to": {"m.in_reply_to": {"event_id": "$prev:example.org"}}
            }
        }
    )

    result = parse_room_message(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.reply_to_event_id == "$prev:example.org"


def test_parse_room_media_image() -> None:
    """Image attachments are parsed."""
    event = FakeRoomMessageImage()

    result = parse_room_media(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.attachments is not None
    assert len(result.attachments) == 1
    assert result.attachments[0].mimetype == "image/png"


def test_parse_room_media_file() -> None:
    """File attachments are parsed."""
    event = FakeRoomMessageFile()

    result = parse_room_media(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.attachments is not None
    assert len(result.attachments) == 1
    assert result.attachments[0].filename == "doc.pdf"


def test_parse_room_audio_voice() -> None:
    """Voice messages are parsed."""
    event = FakeRoomMessageAudio()

    result = parse_room_audio(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.voice is not None
    assert result.voice.mimetype == "audio/ogg"
    assert result.voice.duration_ms == 3000


def test_parse_reaction_emoji() -> None:
    """Standard emoji reaction is parsed."""
    event = FakeReactionEvent(key="👍")

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.key == "👍"
    assert result.target_event_id == "$target:example.org"


def test_parse_reaction_cancel() -> None:
    """Cancel emoji reaction is parsed."""
    event = FakeReactionEvent(key="❌")

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.key == "❌"


def test_parse_reaction_filters_own() -> None:
    """Own reactions are filtered."""
    event = FakeReactionEvent(sender=MATRIX_USER_ID)

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


# --- Helper function tests ---


def test_build_reply_content() -> None:
    """_build_reply_content creates proper reply structure."""
    from takopi_matrix.client import _build_reply_content

    result = _build_reply_content(
        body="reply text",
        formatted_body="<p>reply text</p>",
        reply_to_event_id="$orig:example.org",
    )

    assert result["msgtype"] == "m.text"
    assert result["body"] == "reply text"
    assert result["formatted_body"] == "<p>reply text</p>"
    assert result["format"] == "org.matrix.custom.html"
    assert "m.relates_to" in result
    assert result["m.relates_to"]["m.in_reply_to"]["event_id"] == "$orig:example.org"


def test_build_reply_content_no_formatted() -> None:
    """_build_reply_content works without formatted_body."""
    from takopi_matrix.client import _build_reply_content

    result = _build_reply_content(
        body="plain reply",
        formatted_body=None,
        reply_to_event_id="$orig:example.org",
    )

    assert result["msgtype"] == "m.text"
    assert result["body"] == "plain reply"
    assert "formatted_body" not in result
    assert "format" not in result


def test_build_edit_content() -> None:
    """_build_edit_content creates proper edit structure."""
    from takopi_matrix.client import _build_edit_content

    result = _build_edit_content(
        body="edited text",
        formatted_body="<p>edited text</p>",
        original_event_id="$orig:example.org",
    )

    assert result["msgtype"] == "m.text"
    assert result["body"] == "* edited text"
    assert (
        "formatted_body" not in result
    )  # formatted_body is in m.new_content, not top-level
    assert "m.new_content" in result
    assert result["m.new_content"]["body"] == "edited text"
    assert result["m.new_content"]["formatted_body"] == "<p>edited text</p>"
    assert "m.relates_to" in result
    assert result["m.relates_to"]["rel_type"] == "m.replace"
    assert result["m.relates_to"]["event_id"] == "$orig:example.org"


def test_build_edit_content_no_formatted() -> None:
    """_build_edit_content works without formatted_body."""
    from takopi_matrix.client import _build_edit_content

    result = _build_edit_content(
        body="edited",
        formatted_body=None,
        original_event_id="$orig:example.org",
    )

    assert result["body"] == "* edited"
    assert "formatted_body" not in result  # Top-level
    assert result["m.new_content"]["body"] == "edited"
    assert (
        "formatted_body" not in result["m.new_content"]
    )  # Also not in new_content when None


class TestE2EEAutoTrust:
    """Test end-to-end encryption auto-trust functionality."""

    @pytest.mark.anyio
    async def test_trust_room_devices_trusts_unverified_devices(self) -> None:
        """trust_room_devices calls verify_device for unverified devices."""
        from unittest.mock import MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
            crypto_store_path=None,
        )

        # Mock nio client with device store
        mock_device1 = MagicMock()
        mock_device1.verified = False
        mock_device2 = MagicMock()
        mock_device2.verified = True  # Already verified, should skip

        mock_room = MagicMock()
        mock_room.users = {"@user1:matrix.org", "@user2:matrix.org"}

        mock_nio_client = MagicMock()
        mock_nio_client.rooms = {"!room:matrix.org": mock_room}
        mock_nio_client.device_store = {
            "@user1:matrix.org": {"DEVICE1": mock_device1},
            "@user2:matrix.org": {"DEVICE2": mock_device2},
        }
        mock_nio_client.verify_device = MagicMock()

        # Override the _ensure_nio_client to return our mock
        client._nio_client = mock_nio_client

        await client.trust_room_devices("!room:matrix.org")

        # Should verify device1 but not device2
        assert mock_nio_client.verify_device.call_count == 1
        mock_nio_client.verify_device.assert_called_once_with(mock_device1)

    @pytest.mark.anyio
    async def test_trust_room_devices_handles_missing_room(self) -> None:
        """trust_room_devices handles room not in client.rooms."""
        from unittest.mock import MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
            crypto_store_path=None,
        )

        mock_nio_client = MagicMock()
        mock_nio_client.rooms = {}  # Empty rooms dict
        client._nio_client = mock_nio_client

        # Should not raise, just return early
        await client.trust_room_devices("!nonexistent:matrix.org")

    @pytest.mark.anyio
    async def test_trust_room_devices_handles_errors_gracefully(self) -> None:
        """trust_room_devices logs but doesn't raise on errors."""
        from unittest.mock import MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
            crypto_store_path=None,
        )

        # Mock nio client that raises on rooms access
        mock_nio_client = MagicMock()
        type(mock_nio_client).rooms = MagicMock(side_effect=RuntimeError("Test error"))
        client._nio_client = mock_nio_client

        # Should not raise, error is caught and logged
        await client.trust_room_devices("!room:matrix.org")


class TestReplyTextFetching:
    """Test reply text fetching for resume token extraction."""

    @pytest.mark.anyio
    async def test_get_event_text_returns_body_from_event(self) -> None:
        """get_event_text extracts body from event response."""
        from unittest.mock import AsyncMock, MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
        )
        client._logged_in = True

        # Mock nio client and room_get_event response
        # nio returns Event objects with a 'source' attribute containing raw dict
        mock_nio_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = "200"
        mock_event = MagicMock()
        mock_event.source = {
            "content": {
                "body": "codex:abc123def",
                "msgtype": "m.text",
            }
        }
        mock_response.event = mock_event
        mock_nio_client.room_get_event = AsyncMock(return_value=mock_response)
        client._nio_client = mock_nio_client

        result = await client.get_event_text("!room:matrix.org", "$event123")

        assert result == "codex:abc123def"
        mock_nio_client.room_get_event.assert_called_once_with(
            "!room:matrix.org", "$event123"
        )

    @pytest.mark.anyio
    async def test_get_event_text_returns_none_on_error(self) -> None:
        """get_event_text returns None when event fetch fails."""
        from unittest.mock import AsyncMock, MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
        )
        client._logged_in = True

        # Mock nio client that raises on room_get_event
        mock_nio_client = MagicMock()
        mock_nio_client.room_get_event = AsyncMock(
            side_effect=RuntimeError("Network error")
        )
        client._nio_client = mock_nio_client

        result = await client.get_event_text("!room:matrix.org", "$event123")

        assert result is None

    @pytest.mark.anyio
    async def test_get_event_text_returns_none_when_no_body(self) -> None:
        """get_event_text returns None when event has no body."""
        from unittest.mock import AsyncMock, MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
        )
        client._logged_in = True

        # Mock nio client with event response that has no body
        mock_nio_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = "200"
        mock_event = MagicMock()
        mock_event.source = {
            "content": {
                "msgtype": "m.text",
                # No body field
            }
        }
        # Also ensure the attribute fallback doesn't have body
        mock_event.body = None
        mock_response.event = mock_event
        mock_nio_client.room_get_event = AsyncMock(return_value=mock_response)
        client._nio_client = mock_nio_client

        result = await client.get_event_text("!room:matrix.org", "$event123")

        assert result is None
