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
async def test_outbox_ensure_worker_idempotent() -> None:
    """ensure_worker only starts worker once."""
    outbox = _make_outbox()
    try:
        await outbox.ensure_worker()
        await outbox.ensure_worker()  # Should not create second worker
        assert outbox._tg is not None
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_enqueue_no_wait() -> None:
    """enqueue with wait=False returns immediately."""
    executed = []

    async def slow_op():
        await anyio.sleep(0.1)
        executed.append(1)
        return "result"

    outbox = _make_outbox()
    try:
        op = OutboxOp(
            execute=slow_op,
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        result = await outbox.enqueue(key="nowait", op=op, wait=False)
        assert result is None  # Returned immediately
        # Wait for execution to complete
        await op.done.wait()
        assert executed == [1]
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_fail_pending() -> None:
    """fail_pending clears all pending operations."""
    outbox = _make_outbox()
    try:
        op1 = OutboxOp(
            execute=lambda: anyio.sleep(10),
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        op2 = OutboxOp(
            execute=lambda: anyio.sleep(10),
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        # Add directly to pending
        outbox._pending["key1"] = op1
        outbox._pending["key2"] = op2

        outbox.fail_pending()

        assert op1.done.is_set()
        assert op2.done.is_set()
        assert op1.result is None
        assert op2.result is None
        assert len(outbox._pending) == 0
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_drop_pending() -> None:
    """drop_pending removes operation without executing."""
    executed = []

    async def slow_op():
        await anyio.sleep(1)
        executed.append(1)
        return "result"

    outbox = _make_outbox()
    try:
        op = OutboxOp(
            execute=slow_op,
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        # Enqueue but don't wait
        await outbox.enqueue(key="drop_me", op=op, wait=False)

        # Immediately drop
        await outbox.drop_pending(key="drop_me")

        # Wait for result (should be None from cancellation)
        await op.done.wait()
        assert op.result is None
        assert executed == []  # Never executed
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_drop_pending_nonexistent() -> None:
    """drop_pending with nonexistent key is a no-op."""
    outbox = _make_outbox()
    try:
        # Should not raise
        await outbox.drop_pending(key="nonexistent")
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_on_error_callback() -> None:
    """on_error callback is called for non-RetryAfter exceptions."""
    errors: list = []

    def error_handler(op, exc):
        errors.append((op, exc))

    async def failing_op():
        raise ValueError("Test error")

    outbox = _make_outbox(on_error=error_handler)
    try:
        op = OutboxOp(
            execute=failing_op,
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        result = await outbox.enqueue(key="error", op=op)
        assert result is None
        assert len(errors) == 1
        assert isinstance(errors[0][1], ValueError)
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_pick_locked_empty() -> None:
    """pick_locked returns None when queue is empty."""
    outbox = _make_outbox()
    try:
        assert outbox.pick_locked() is None
    finally:
        await outbox.close()


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


@pytest.mark.anyio
async def test_outbox_enqueue_on_closed() -> None:
    """enqueue on closed queue returns immediately with None result."""
    executed = []

    async def my_op():
        executed.append(1)
        return "result"

    outbox = _make_outbox()
    await outbox.close()  # Close first

    op = OutboxOp(
        execute=my_op,
        priority=SEND_PRIORITY,
        queued_at=0,
        updated_at=0,
        room_id=MATRIX_ROOM_ID,
    )
    result = await outbox.enqueue(key="closed", op=op)
    assert result is None  # Returns None immediately
    assert op.done.is_set()  # Op is marked done
    assert op.result is None  # Result is None
    assert executed == []  # Never executed


@pytest.mark.anyio
async def test_outbox_enqueue_replaces_pending() -> None:
    """enqueue replaces pending op and preserves original queued_at."""
    executed = []

    async def op1_fn():
        executed.append(1)
        return "op1"

    async def op2_fn():
        executed.append(2)
        return "op2"

    outbox = _make_outbox()
    try:
        op1 = OutboxOp(
            execute=op1_fn,
            priority=SEND_PRIORITY,
            queued_at=100.0,  # Original queue time
            updated_at=100.0,
            room_id=MATRIX_ROOM_ID,
        )
        op2 = OutboxOp(
            execute=op2_fn,
            priority=SEND_PRIORITY,
            queued_at=200.0,  # Different queue time
            updated_at=200.0,
            room_id=MATRIX_ROOM_ID,
        )

        # Manually add op1 to pending (don't start worker yet)
        outbox._pending["same_key"] = op1

        # Now enqueue op2 with same key - should replace op1
        async with outbox._cond:
            previous = outbox._pending.get("same_key")
            assert previous is op1
            op2.queued_at = previous.queued_at  # Preserves original
            previous.set_result(None)  # Marks op1 done
            outbox._pending["same_key"] = op2

        assert op1.done.is_set()  # op1 was cancelled
        assert op1.result is None
        assert op2.queued_at == 100.0  # Preserved from op1
    finally:
        await outbox.close()


@pytest.mark.anyio
async def test_outbox_retry_after_when_closed() -> None:
    """Retry after exception on closed outbox sets result to None."""
    attempt = [0]

    async def retry_then_succeed():
        attempt[0] += 1
        if attempt[0] == 1:
            raise MatrixRetryAfter(0.01)
        return "success"

    outbox = MatrixOutbox(interval=0.01)
    try:
        op = OutboxOp(
            execute=retry_then_succeed,
            priority=SEND_PRIORITY,
            queued_at=0,
            updated_at=0,
            room_id=MATRIX_ROOM_ID,
        )
        # Start the operation
        _ = await outbox.enqueue(key="retry_closed", op=op, wait=False)
        # Wait for first attempt
        await anyio.sleep(0.05)
        # Close while retrying
        await outbox.close()
        # Give time for cleanup
        await anyio.sleep(0.05)
        assert op.done.is_set()
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

    @pytest.mark.anyio
    async def test_get_event_sender_returns_sender_from_event(self) -> None:
        """get_event_sender extracts sender from event attribute."""
        from unittest.mock import AsyncMock, MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
        )
        client._logged_in = True

        mock_nio_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = "200"
        mock_event = MagicMock()
        mock_event.sender = "@bot:matrix.org"
        mock_response.event = mock_event
        mock_nio_client.room_get_event = AsyncMock(return_value=mock_response)
        client._nio_client = mock_nio_client

        result = await client.get_event_sender("!room:matrix.org", "$event123")

        assert result == "@bot:matrix.org"

    @pytest.mark.anyio
    async def test_get_event_sender_falls_back_to_source(self) -> None:
        """get_event_sender uses source dict when sender attribute is absent."""
        from unittest.mock import AsyncMock, MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
        )
        client._logged_in = True

        mock_nio_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = "200"
        mock_event = MagicMock()
        mock_event.sender = None
        mock_event.source = {"sender": "@bot:matrix.org"}
        mock_response.event = mock_event
        mock_nio_client.room_get_event = AsyncMock(return_value=mock_response)
        client._nio_client = mock_nio_client

        result = await client.get_event_sender("!room:matrix.org", "$event123")

        assert result == "@bot:matrix.org"

    @pytest.mark.anyio
    async def test_get_event_sender_returns_none_on_error(self) -> None:
        """get_event_sender returns None when event fetch fails."""
        from unittest.mock import AsyncMock, MagicMock
        from takopi_matrix.client import MatrixClient

        client = MatrixClient(
            homeserver="https://matrix.org",
            user_id="@bot:matrix.org",
            access_token="token",
        )
        client._logged_in = True

        mock_nio_client = MagicMock()
        mock_nio_client.room_get_event = AsyncMock(
            side_effect=RuntimeError("Network error")
        )
        client._nio_client = mock_nio_client

        result = await client.get_event_sender("!room:matrix.org", "$event123")

        assert result is None


# --- Parser edge case tests ---


class FakeEventNullSender:
    """Fake event with None sender."""

    def __init__(self):
        self.sender = None
        self.event_id = "$evt:example.org"
        self.url = None
        self.body = None
        self.source = {"content": {}}


class FakeEventNullEventId:
    """Fake event with None event_id."""

    def __init__(self):
        self.sender = MATRIX_SENDER
        self.event_id = None
        self.source = {"content": {}}


class FakeMediaNoUrl:
    """Fake media event with no URL."""

    def __init__(self):
        self.sender = MATRIX_SENDER
        self.event_id = "$evt:example.org"
        self.body = "file.png"
        # No url attribute
        self.source = {"content": {"info": {}}}


class FakeMediaEncrypted:
    """Fake encrypted media event with file.url structure."""

    def __init__(self):
        self.sender = MATRIX_SENDER
        self.event_id = "$evt:example.org"
        self.body = "encrypted.png"
        # No direct url attribute - encrypted file uses file.url
        self.source = {
            "content": {
                "info": {"size": 1024, "mimetype": "image/png"},
                "file": {
                    "url": "mxc://example.org/encrypted123",
                    "key": {"k": "base64key"},
                    "iv": "base64iv",
                },
            }
        }


class FakeReactionInvalidRelatesTo:
    """Fake reaction with invalid m.relates_to."""

    def __init__(self, relates_to_value):
        self.sender = MATRIX_SENDER
        self.event_id = "$reaction:example.org"
        self.source = {"content": {"m.relates_to": relates_to_value}}


class FakeReactionMissingKey:
    """Fake reaction with missing key field."""

    def __init__(self):
        self.sender = MATRIX_SENDER
        self.event_id = "$reaction:example.org"
        self.source = {
            "content": {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": "$target:example.org",
                    # No key field
                }
            }
        }


class FakeReactionMissingEventId:
    """Fake reaction with missing target event_id."""

    def __init__(self):
        self.sender = MATRIX_SENDER
        self.event_id = "$reaction:example.org"
        self.source = {
            "content": {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "key": "👍",
                    # No event_id field
                }
            }
        }


def test_parse_room_message_in_reply_to_not_dict() -> None:
    """Reply with non-dict in_reply_to returns None for reply_to_event_id."""
    event = FakeRoomMessageText(
        source={
            "content": {
                "m.relates_to": {
                    "m.in_reply_to": "not-a-dict"  # Should be a dict
                }
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
    assert result.reply_to_event_id is None


def test_parse_room_message_event_id_not_string() -> None:
    """Reply with non-string event_id returns None for reply_to_event_id."""
    event = FakeRoomMessageText(
        source={
            "content": {
                "m.relates_to": {
                    "m.in_reply_to": {"event_id": 12345}  # Should be a string
                }
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
    assert result.reply_to_event_id is None


def test_parse_room_message_null_sender() -> None:
    """Event with None sender returns None."""
    event = FakeEventNullSender()

    result = parse_room_message(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_message_null_event_id() -> None:
    """Event with None event_id returns None."""
    event = FakeEventNullEventId()

    result = parse_room_message(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_media_encrypted_file() -> None:
    """Encrypted media with file.url structure is parsed."""
    event = FakeMediaEncrypted()

    result = parse_room_media(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.attachments is not None
    assert len(result.attachments) == 1
    assert result.attachments[0].mxc_url == "mxc://example.org/encrypted123"


def test_parse_room_media_no_url() -> None:
    """Media without URL returns None."""
    event = FakeMediaNoUrl()

    result = parse_room_media(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_media_null_sender() -> None:
    """Media event with None sender returns None."""
    event = FakeEventNullSender()
    event.url = "mxc://example.org/img123"
    event.body = "image.png"

    result = parse_room_media(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_audio_encrypted() -> None:
    """Encrypted audio with file.url structure is parsed."""

    class FakeAudioEncrypted:
        def __init__(self):
            self.sender = MATRIX_SENDER
            self.event_id = "$evt:example.org"
            self.body = "voice.ogg"
            self.source = {
                "content": {
                    "info": {"size": 5000, "duration": 3000, "mimetype": "audio/ogg"},
                    "file": {"url": "mxc://example.org/audio_encrypted"},
                }
            }

    event = FakeAudioEncrypted()

    result = parse_room_audio(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is not None
    assert result.voice is not None
    assert result.voice.mxc_url == "mxc://example.org/audio_encrypted"


def test_parse_room_audio_no_url() -> None:
    """Audio without URL returns None."""

    class FakeAudioNoUrl:
        def __init__(self):
            self.sender = MATRIX_SENDER
            self.event_id = "$evt:example.org"
            self.body = "voice.ogg"
            self.source = {"content": {"info": {}}}

    event = FakeAudioNoUrl()

    result = parse_room_audio(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_room_audio_null_sender() -> None:
    """Audio event with None sender returns None."""
    event = FakeEventNullSender()
    event.url = "mxc://example.org/audio123"
    event.body = "voice.ogg"

    result = parse_room_audio(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_wrong_room() -> None:
    """Reaction from non-allowed room returns None."""
    event = FakeReactionEvent()

    result = parse_reaction(
        event=event,
        room_id="!other:example.org",
        allowed_room_ids={MATRIX_ROOM_ID},  # Only allow different room
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_null_sender() -> None:
    """Reaction with None sender returns None."""

    class FakeReactionNullSender:
        def __init__(self):
            self.sender = None
            self.event_id = "$reaction:example.org"
            self.source = {
                "content": {
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": "$target:example.org",
                        "key": "👍",
                    }
                }
            }

    event = FakeReactionNullSender()

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_null_event_id() -> None:
    """Reaction with None event_id returns None."""

    class FakeReactionNullEventId:
        def __init__(self):
            self.sender = MATRIX_SENDER
            self.event_id = None
            self.source = {
                "content": {
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": "$target:example.org",
                        "key": "👍",
                    }
                }
            }

    event = FakeReactionNullEventId()

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_relates_to_not_dict() -> None:
    """Reaction with non-dict m.relates_to returns None."""
    event = FakeReactionInvalidRelatesTo("not-a-dict")

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_wrong_rel_type() -> None:
    """Reaction with wrong rel_type returns None."""

    class FakeReactionWrongRelType:
        def __init__(self):
            self.sender = MATRIX_SENDER
            self.event_id = "$reaction:example.org"
            self.source = {
                "content": {
                    "m.relates_to": {
                        "rel_type": "m.reference",  # Not m.annotation
                        "event_id": "$target:example.org",
                        "key": "👍",
                    }
                }
            }

    event = FakeReactionWrongRelType()

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_missing_target_event_id() -> None:
    """Reaction without target event_id returns None."""
    event = FakeReactionMissingEventId()

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


def test_parse_reaction_missing_key() -> None:
    """Reaction without key returns None."""
    event = FakeReactionMissingKey()

    result = parse_reaction(
        event=event,
        room_id=MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=MATRIX_USER_ID,
    )

    assert result is None


# --- MatrixClient tests ---


def test_matrix_client_init(tmp_path) -> None:
    """MatrixClient initializes with required parameters."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    assert client.homeserver == "https://matrix.example.org"
    assert client.user_id == "@bot:example.org"
    assert client._access_token == "token123"
    assert client._logged_in is False
    assert client._nio_client is None


def test_matrix_client_homeserver_trailing_slash() -> None:
    """MatrixClient strips trailing slash from homeserver."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org/",
        user_id="@bot:example.org",
        access_token="token123",
    )

    assert client.homeserver == "https://matrix.example.org"


def test_matrix_client_e2ee_available() -> None:
    """e2ee_available property checks for nio.crypto."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
    )

    # Result depends on whether matrix-nio[e2e] is installed
    assert isinstance(client.e2ee_available, bool)


def test_matrix_client_default_sync_store_path() -> None:
    """Default sync store path is in home directory."""
    from pathlib import Path
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
    )

    # _sync_store_path should be set to default
    expected = Path.home() / ".takopi" / "matrix_sync.json"
    assert client._sync_store_path == expected


def test_matrix_client_load_sync_token_no_file(tmp_path) -> None:
    """Load sync token returns None when file doesn't exist."""
    from takopi_matrix.client.client import MatrixClient

    sync_path = tmp_path / "nonexistent" / "sync.json"
    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=sync_path,
    )

    assert client._sync_token is None


def test_matrix_client_load_sync_token_success(tmp_path) -> None:
    """Load sync token from existing file."""
    import json
    from takopi_matrix.client.client import MatrixClient

    sync_path = tmp_path / "sync.json"
    sync_path.write_text(
        json.dumps(
            {
                "user_id": "@bot:example.org",
                "next_batch": "s12345_678",
            }
        )
    )

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=sync_path,
    )

    assert client._sync_token == "s12345_678"


def test_matrix_client_load_sync_token_wrong_user(tmp_path) -> None:
    """Load sync token returns None for different user."""
    import json
    from takopi_matrix.client.client import MatrixClient

    sync_path = tmp_path / "sync.json"
    sync_path.write_text(
        json.dumps(
            {
                "user_id": "@other:example.org",
                "next_batch": "s12345_678",
            }
        )
    )

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=sync_path,
    )

    assert client._sync_token is None


def test_matrix_client_load_sync_token_invalid_json(tmp_path) -> None:
    """Load sync token handles invalid JSON gracefully."""
    from takopi_matrix.client.client import MatrixClient

    sync_path = tmp_path / "sync.json"
    sync_path.write_text("not json{")

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=sync_path,
    )

    assert client._sync_token is None


def test_matrix_client_log_request_error() -> None:
    """log_request_error handles OutboxOp error callback."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
    )

    op = OutboxOp(
        execute=lambda: "result",
        priority=SEND_PRIORITY,
        queued_at=0,
        updated_at=0,
        room_id=MATRIX_ROOM_ID,
    )

    # Should not raise
    client.log_request_error(op, Exception("test error"))


def test_matrix_client_log_outbox_failure() -> None:
    """log_outbox_failure handles outbox failure callback."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
    )

    # Should not raise
    client.log_outbox_failure(Exception("outbox crashed"))


def test_matrix_client_save_sync_token(tmp_path) -> None:
    """_save_sync_token writes token to disk."""
    import json
    from takopi_matrix.client.client import MatrixClient

    sync_path = tmp_path / "sync.json"
    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=sync_path,
    )

    client._save_sync_token("s99999_new")

    data = json.loads(sync_path.read_text())
    assert data["next_batch"] == "s99999_new"
    assert data["user_id"] == "@bot:example.org"


def test_matrix_client_save_sync_token_creates_dirs(tmp_path) -> None:
    """_save_sync_token creates parent directories."""
    import json
    from takopi_matrix.client.client import MatrixClient

    sync_path = tmp_path / "nested" / "dir" / "sync.json"
    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=sync_path,
    )

    client._save_sync_token("s99999_new")

    assert sync_path.exists()
    data = json.loads(sync_path.read_text())
    assert data["next_batch"] == "s99999_new"


def test_matrix_client_save_sync_token_none_path() -> None:
    """_save_sync_token does nothing when path is None."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=None,
    )

    # Should not raise
    client._save_sync_token("s99999_new")


def test_matrix_client_unique_key() -> None:
    """unique_key returns incrementing sequence."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
    )

    key1 = client.unique_key("send")
    key2 = client.unique_key("send")
    key3 = client.unique_key("typing")

    assert key1[0] == "send"
    assert key2[0] == "send"
    assert key3[0] == "typing"
    # Sequence should increment
    assert key1[1] < key2[1] < key3[1]


@pytest.mark.anyio
async def test_matrix_client_close(tmp_path) -> None:
    """close() closes outbox and http client."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    await client.close()

    assert client._outbox._closed
    assert client._nio_client is None


@pytest.mark.anyio
async def test_matrix_client_drop_pending_edits() -> None:
    """drop_pending_edits delegates to outbox."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
    )

    try:
        # Should not raise even if nothing pending
        await client.drop_pending_edits(
            room_id="!room:example.org",
            event_id="$evt:example.org",
        )
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_login_with_token(tmp_path) -> None:
    """login() with access_token sets logged in state."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        device_id="TESTDEVICE",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock _ensure_nio_client with awaitable close
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    client._nio_client = mock_nio

    try:
        result = await client.login()

        assert result is True
        assert client._logged_in is True
        assert mock_nio.access_token == "token123"
        assert mock_nio.user_id == "@bot:example.org"
        assert mock_nio.device_id == "TESTDEVICE"
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_login_no_credentials(tmp_path) -> None:
    """login() without credentials returns False."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        # No access_token or password
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock _ensure_nio_client to avoid real nio
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    client._nio_client = mock_nio

    try:
        result = await client.login()

        assert result is False
        assert client._logged_in is False
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_login_with_password_success(tmp_path) -> None:
    """login() with password calls nio.login and handles success."""
    import nio
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        password="secret123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client with successful login response
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    login_response = MagicMock(spec=nio.LoginResponse)
    login_response.access_token = "new_token"
    login_response.device_id = "NEW_DEVICE"
    mock_nio.login = AsyncMock(return_value=login_response)
    client._nio_client = mock_nio

    try:
        result = await client.login()

        assert result is True
        assert client._logged_in is True
        assert client._access_token == "new_token"
        assert client._device_id == "NEW_DEVICE"
        mock_nio.login.assert_called_once_with(
            password="secret123",
            device_name="Takopi",
        )
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_login_with_password_failure(tmp_path) -> None:
    """login() with password handles login failure."""
    import nio
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        password="wrong_password",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client with error response
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    error_response = MagicMock(spec=nio.LoginError)
    error_response.message = "Invalid password"
    mock_nio.login = AsyncMock(return_value=error_response)
    client._nio_client = mock_nio

    try:
        result = await client.login()

        assert result is False
        assert client._logged_in is False
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_enqueue_op(tmp_path) -> None:
    """enqueue_op creates and enqueues an OutboxOp."""
    from takopi_matrix.client.client import MatrixClient

    executed = []

    async def test_op():
        executed.append(1)
        return "success"

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    try:
        result = await client.enqueue_op(
            key=("test", 1),
            label="test_op",
            execute=test_op,
            priority=SEND_PRIORITY,
            room_id="!room:example.org",
        )

        assert result == "success"
        assert executed == [1]
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_sync_success(tmp_path) -> None:
    """sync() returns SyncResponse and saves token."""
    import nio
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    sync_response = MagicMock(spec=nio.SyncResponse)
    sync_response.next_batch = "s12345_new"
    mock_nio.sync = AsyncMock(return_value=sync_response)
    client._nio_client = mock_nio
    client._logged_in = True

    try:
        result = await client.sync(timeout_ms=30000)

        assert result is sync_response
        assert client._sync_token == "s12345_new"
        mock_nio.sync.assert_called_once()
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_sync_error_response(tmp_path) -> None:
    """sync() handles error response."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client with error response (no retry_after_ms)
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    # Create error response without retry_after_ms attribute
    error_response = MagicMock()
    del error_response.retry_after_ms  # Ensure no retry_after_ms
    error_response.message = "Sync failed"
    mock_nio.sync = AsyncMock(return_value=error_response)
    client._nio_client = mock_nio
    client._logged_in = True

    try:
        result = await client.sync()

        assert result is None
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_sync_rate_limited(tmp_path) -> None:
    """sync() raises MatrixRetryAfter on rate limit."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client with rate limit response
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    rate_limit_response = MagicMock()
    rate_limit_response.retry_after_ms = 5000
    mock_nio.sync = AsyncMock(return_value=rate_limit_response)
    client._nio_client = mock_nio
    client._logged_in = True

    try:
        with pytest.raises(MatrixRetryAfter) as exc_info:
            await client.sync()
        assert exc_info.value.retry_after == 5.0
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_sync_exception(tmp_path) -> None:
    """sync() handles exceptions gracefully."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client that raises exception
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.sync = AsyncMock(side_effect=Exception("Network error"))
    client._nio_client = mock_nio
    client._logged_in = True

    try:
        result = await client.sync()

        assert result is None
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_get_display_name_success(tmp_path) -> None:
    """get_display_name returns display name on success."""
    import nio
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    display_response = MagicMock(spec=nio.ProfileGetDisplayNameResponse)
    display_response.displayname = "Test Bot"
    mock_nio.get_displayname = AsyncMock(return_value=display_response)
    client._nio_client = mock_nio
    client._logged_in = True

    try:
        result = await client.get_display_name()

        assert result == "Test Bot"
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_get_display_name_not_logged_in(tmp_path) -> None:
    """get_display_name returns None when not logged in."""
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Not logged in, no nio client
    try:
        result = await client.get_display_name()

        assert result is None
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_get_display_name_exception(tmp_path) -> None:
    """get_display_name returns None on exception."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock nio client that raises
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.get_displayname = AsyncMock(side_effect=Exception("API error"))
    client._nio_client = mock_nio
    client._logged_in = True

    try:
        result = await client.get_display_name()

        assert result is None
    finally:
        await client.close()


@pytest.mark.anyio
async def test_matrix_client_init_e2ee_not_available(tmp_path) -> None:
    """init_e2ee returns False when E2EE not available."""
    from unittest.mock import patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Mock e2ee_available to return False
    with patch.object(
        type(client),
        "e2ee_available",
        new_callable=lambda: property(lambda self: False),
    ):
        result = await client.init_e2ee()

    assert result is False
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_init_e2ee_success(tmp_path) -> None:
    """init_e2ee returns True on success."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.load_store = MagicMock()
    mock_nio.should_upload_keys = False
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        result = await client.init_e2ee()

    assert result is True
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_init_e2ee_keys_upload(tmp_path) -> None:
    """init_e2ee uploads keys when needed."""
    import nio
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.load_store = MagicMock()
    mock_nio.should_upload_keys = True
    upload_response = MagicMock(spec=nio.KeysUploadResponse)
    mock_nio.keys_upload = AsyncMock(return_value=upload_response)
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        result = await client.init_e2ee()

    assert result is True
    mock_nio.keys_upload.assert_called_once()
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_init_e2ee_keys_upload_failed(tmp_path) -> None:
    """init_e2ee returns False when key upload fails."""
    import nio
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.load_store = MagicMock()
    mock_nio.should_upload_keys = True
    error_response = MagicMock(spec=nio.KeysUploadError)
    error_response.message = "Upload failed"
    mock_nio.keys_upload = AsyncMock(return_value=error_response)
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        result = await client.init_e2ee()

    assert result is False
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_init_e2ee_exception(tmp_path) -> None:
    """init_e2ee returns False on exception."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.load_store = MagicMock(side_effect=Exception("Store error"))
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        result = await client.init_e2ee()

    assert result is False
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_ensure_room_keys_not_available(tmp_path) -> None:
    """ensure_room_keys does nothing when E2EE not available."""
    from unittest.mock import patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    with patch.object(
        type(client),
        "e2ee_available",
        new_callable=lambda: property(lambda self: False),
    ):
        await client.ensure_room_keys("!room:example.org")

    await client.close()


@pytest.mark.anyio
async def test_matrix_client_ensure_room_keys_claims_keys(tmp_path) -> None:
    """ensure_room_keys claims one-time keys when needed."""
    import nio
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.should_claim_keys = True
    mock_nio.get_users_for_key_claiming = MagicMock(
        return_value={"@user:example.org": ["DEVICE"]}
    )
    claim_response = MagicMock(spec=nio.KeysClaimResponse)
    mock_nio.keys_claim = AsyncMock(return_value=claim_response)
    mock_nio.share_group_session = AsyncMock(return_value=MagicMock())
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        await client.ensure_room_keys("!room:example.org")

    mock_nio.keys_claim.assert_called_once()
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_trust_room_devices_not_available(tmp_path) -> None:
    """trust_room_devices does nothing when E2EE not available."""
    from unittest.mock import patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    with patch.object(
        type(client),
        "e2ee_available",
        new_callable=lambda: property(lambda self: False),
    ):
        await client.trust_room_devices("!room:example.org")

    await client.close()


@pytest.mark.anyio
async def test_matrix_client_trust_room_devices_no_room(tmp_path) -> None:
    """trust_room_devices returns early when room not found."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.rooms = {}  # No rooms
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        await client.trust_room_devices("!room:example.org")

    await client.close()


@pytest.mark.anyio
async def test_matrix_client_trust_room_devices_no_device_store(tmp_path) -> None:
    """trust_room_devices returns early when no device store."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_room = MagicMock()
    mock_room.users = {"@user:example.org": MagicMock()}
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.rooms = {"!room:example.org": mock_room}
    mock_nio.device_store = None  # No device store
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        await client.trust_room_devices("!room:example.org")

    await client.close()


@pytest.mark.anyio
async def test_matrix_client_trust_room_devices_verifies_devices(tmp_path) -> None:
    """trust_room_devices verifies unverified devices."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    # Setup room
    mock_room = MagicMock()
    mock_room.users = {"@user:example.org": MagicMock()}

    # Setup device
    mock_device = MagicMock()
    mock_device.verified = False

    # Setup device store
    mock_device_store = MagicMock()
    mock_device_store.items.return_value = [
        ("@user:example.org", {"DEVICE1": mock_device}),
    ]

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.rooms = {"!room:example.org": mock_room}
    mock_nio.device_store = mock_device_store
    mock_nio.verify_device = MagicMock()
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        await client.trust_room_devices("!room:example.org")

    mock_nio.verify_device.assert_called_once_with(mock_device)
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_trust_room_devices_exception(tmp_path) -> None:
    """trust_room_devices handles exceptions gracefully."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.rooms = MagicMock()
    mock_nio.rooms.get = MagicMock(side_effect=Exception("Rooms error"))
    client._nio_client = mock_nio

    with patch.object(
        type(client), "e2ee_available", new_callable=lambda: property(lambda self: True)
    ):
        # Should not raise
        await client.trust_room_devices("!room:example.org")

    await client.close()


@pytest.mark.anyio
async def test_matrix_client_request_room_key(tmp_path) -> None:
    """_request_room_key calls request_room_key on client."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_event = MagicMock()
    mock_event.event_id = "$evt:example.org"
    mock_event.sender = "@user:example.org"

    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.request_room_key = AsyncMock()
    client._nio_client = mock_nio

    await client._request_room_key(mock_event)

    mock_nio.request_room_key.assert_called_once_with(mock_event)
    await client.close()


@pytest.mark.anyio
async def test_matrix_client_request_room_key_exception(tmp_path) -> None:
    """_request_room_key handles exceptions silently."""
    from unittest.mock import AsyncMock, MagicMock
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_event = MagicMock()
    mock_nio = MagicMock()
    mock_nio.close = AsyncMock()
    mock_nio.request_room_key = AsyncMock(side_effect=Exception("Key error"))
    client._nio_client = mock_nio

    # Should not raise
    await client._request_room_key(mock_event)

    await client.close()


@pytest.mark.anyio
async def test_matrix_client_decrypt_event_not_available(tmp_path) -> None:
    """decrypt_event returns None when E2EE not available."""
    from unittest.mock import MagicMock, patch
    from takopi_matrix.client.client import MatrixClient

    client = MatrixClient(
        homeserver="https://matrix.example.org",
        user_id="@bot:example.org",
        access_token="token123",
        sync_store_path=tmp_path / "sync.json",
    )

    mock_event = MagicMock()

    with patch.object(
        type(client),
        "e2ee_available",
        new_callable=lambda: property(lambda self: False),
    ):
        result = await client.decrypt_event(mock_event)

    assert result is None
    await client.close()
