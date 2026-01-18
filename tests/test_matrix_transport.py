"""Tests for bridge/transport.py - Matrix transport wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from takopi.api import MessageRef, RenderedMessage, SendOptions
from takopi_matrix.bridge.transport import MatrixTransport


class FakeMatrixClient:
    """Fake MatrixClient for testing transport."""

    def __init__(self):
        self.send_message_calls: list[dict] = []
        self.edit_message_calls: list[dict] = []
        self.redact_message_calls: list[dict] = []
        self.drop_pending_calls: list[dict] = []

        self._send_result = {"event_id": "$sent123"}
        self._edit_result = {"event_id": "$edited123"}
        self._redact_result = True

    async def send_message(
        self,
        *,
        room_id: str,
        body: str,
        formatted_body: str | None = None,
        reply_to_event_id: str | None = None,
        disable_notification: bool = False,
    ) -> dict | None:
        self.send_message_calls.append({
            "room_id": room_id,
            "body": body,
            "formatted_body": formatted_body,
            "reply_to_event_id": reply_to_event_id,
            "disable_notification": disable_notification,
        })
        return self._send_result

    async def edit_message(
        self,
        *,
        room_id: str,
        event_id: str,
        body: str,
        formatted_body: str | None = None,
        wait: bool = True,
    ) -> dict | None:
        self.edit_message_calls.append({
            "room_id": room_id,
            "event_id": event_id,
            "body": body,
            "formatted_body": formatted_body,
            "wait": wait,
        })
        return self._edit_result

    async def redact_message(self, *, room_id: str, event_id: str) -> bool:
        self.redact_message_calls.append({
            "room_id": room_id,
            "event_id": event_id,
        })
        return self._redact_result

    async def drop_pending_edits(self, *, room_id: str, event_id: str) -> None:
        self.drop_pending_calls.append({
            "room_id": room_id,
            "event_id": event_id,
        })

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_client() -> FakeMatrixClient:
    return FakeMatrixClient()


@pytest.fixture
def transport(fake_client: FakeMatrixClient) -> MatrixTransport:
    return MatrixTransport(fake_client)


# --- send tests ---


@pytest.mark.anyio
async def test_send_basic_message(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Basic message send works."""
    message = RenderedMessage(text="Hello, world!")

    result = await transport.send(channel_id="!room:example.org", message=message)

    assert result is not None
    assert result.message_id == "$sent123"
    assert len(fake_client.send_message_calls) == 1
    assert fake_client.send_message_calls[0]["body"] == "Hello, world!"


@pytest.mark.anyio
async def test_send_with_reply_options(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Send with reply_to options."""
    message = RenderedMessage(text="Reply text")
    reply_ref = MessageRef(channel_id="!room:example.org", message_id="$original")
    options = SendOptions(notify=True, reply_to=reply_ref)

    await transport.send(
        channel_id="!room:example.org", message=message, options=options
    )

    assert fake_client.send_message_calls[0]["reply_to_event_id"] == "$original"


@pytest.mark.anyio
async def test_send_with_notify_false(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Send with notify=False disables notification."""
    message = RenderedMessage(text="Silent message")
    options = SendOptions(notify=False)

    await transport.send(
        channel_id="!room:example.org", message=message, options=options
    )

    assert fake_client.send_message_calls[0]["disable_notification"] is True


@pytest.mark.anyio
async def test_send_with_replace_drops_edits_and_redacts(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Send with replace drops pending edits and redacts old message."""
    message = RenderedMessage(text="Replacement message")
    old_ref = MessageRef(channel_id="!room:example.org", message_id="$old123")
    options = SendOptions(replace=old_ref)

    await transport.send(
        channel_id="!room:example.org", message=message, options=options
    )

    # Should have dropped pending edits for old message
    assert len(fake_client.drop_pending_calls) == 1
    assert fake_client.drop_pending_calls[0]["event_id"] == "$old123"

    # Should have redacted old message
    assert len(fake_client.redact_message_calls) == 1
    assert fake_client.redact_message_calls[0]["event_id"] == "$old123"


@pytest.mark.anyio
async def test_send_returns_none_when_send_fails(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Send returns None when client.send_message returns None."""
    fake_client._send_result = None
    message = RenderedMessage(text="Will fail")

    result = await transport.send(channel_id="!room:example.org", message=message)

    assert result is None


@pytest.mark.anyio
async def test_send_returns_none_when_no_event_id(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Send returns None when response has no event_id."""
    fake_client._send_result = {}  # No event_id key
    message = RenderedMessage(text="Will fail")

    result = await transport.send(channel_id="!room:example.org", message=message)

    assert result is None


# --- edit tests ---


@pytest.mark.anyio
async def test_edit_basic(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Basic message edit works."""
    ref = MessageRef(channel_id="!room:example.org", message_id="$orig123")
    message = RenderedMessage(text="Edited text")

    result = await transport.edit(ref=ref, message=message)

    assert result is not None
    assert result.message_id == "$edited123"
    assert len(fake_client.edit_message_calls) == 1


@pytest.mark.anyio
async def test_edit_returns_none_when_edit_fails(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Edit returns None when client.edit_message returns None."""
    fake_client._edit_result = None
    ref = MessageRef(channel_id="!room:example.org", message_id="$orig123")
    message = RenderedMessage(text="Edited text")

    result = await transport.edit(ref=ref, message=message, wait=True)

    assert result is None


@pytest.mark.anyio
async def test_edit_returns_ref_when_not_waiting(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Edit returns original ref when wait=False and edit returns None."""
    fake_client._edit_result = None
    ref = MessageRef(channel_id="!room:example.org", message_id="$orig123")
    message = RenderedMessage(text="Edited text")

    result = await transport.edit(ref=ref, message=message, wait=False)

    assert result is not None
    assert result.message_id == "$orig123"  # Returns original ref


# --- delete tests ---


@pytest.mark.anyio
async def test_delete_basic(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Basic message delete works."""
    ref = MessageRef(channel_id="!room:example.org", message_id="$del123")

    result = await transport.delete(ref=ref)

    assert result is True
    assert len(fake_client.redact_message_calls) == 1
    assert fake_client.redact_message_calls[0]["event_id"] == "$del123"


# --- close tests ---


@pytest.mark.anyio
async def test_close(
    transport: MatrixTransport, fake_client: FakeMatrixClient
) -> None:
    """Close delegates to client."""
    await transport.close()
    # Should not raise
