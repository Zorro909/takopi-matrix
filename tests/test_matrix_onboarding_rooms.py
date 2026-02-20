"""Tests for onboarding/rooms.py - room invite handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import nio

from takopi_matrix.onboarding.rooms import (
    RoomInvite,
    _fetch_room_invites,
    _accept_room_invite,
    _wait_for_room,
    _send_confirmation,
)


# --- RoomInvite dataclass tests ---


def test_room_invite_dataclass() -> None:
    """RoomInvite stores room details."""
    invite = RoomInvite(
        room_id="!room:example.org",
        inviter="@user:example.org",
        room_name="Test Room",
    )
    assert invite.room_id == "!room:example.org"
    assert invite.inviter == "@user:example.org"
    assert invite.room_name == "Test Room"


def test_room_invite_with_none_values() -> None:
    """RoomInvite handles None values."""
    invite = RoomInvite(
        room_id="!room:example.org",
        inviter=None,
        room_name=None,
    )
    assert invite.room_id == "!room:example.org"
    assert invite.inviter is None
    assert invite.room_name is None


# --- _fetch_room_invites tests ---


class FakeInviteState:
    """Fake invite state event."""

    def __init__(self, sender: str | None = None, name: str | None = None):
        if sender:
            self.sender = sender
        if name:
            self.name = name


class FakeInviteInfo:
    """Fake room invite info."""

    def __init__(self, events: list):
        self.invite_state = events


class FakeRooms:
    """Fake rooms in sync response."""

    def __init__(self, invite: dict):
        self.invite = invite
        self.join = {}


class FakeSyncResponse:
    """Fake nio.SyncResponse."""

    def __init__(self, rooms: FakeRooms, next_batch: str = "batch1"):
        self.rooms = rooms
        self.next_batch = next_batch


@pytest.mark.anyio
async def test_fetch_room_invites_success() -> None:
    """Fetch room invites returns list of RoomInvite objects."""
    invite_info = FakeInviteInfo(
        [
            FakeInviteState(sender="@inviter:example.org", name="Cool Room"),
        ]
    )
    rooms = FakeRooms(invite={"!room1:example.org": invite_info})

    # Create a mock that passes isinstance check
    sync_response = MagicMock(spec=nio.SyncResponse)
    sync_response.rooms = rooms

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(return_value=sync_response)
        mock_client.close = AsyncMock()

        result = await _fetch_room_invites(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert len(result) == 1
    assert result[0].room_id == "!room1:example.org"
    assert result[0].inviter == "@inviter:example.org"
    assert result[0].room_name == "Cool Room"


@pytest.mark.anyio
async def test_fetch_room_invites_no_invites() -> None:
    """Fetch room invites returns empty list when no invites."""
    rooms = FakeRooms(invite={})

    sync_response = MagicMock(spec=nio.SyncResponse)
    sync_response.rooms = rooms

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(return_value=sync_response)
        mock_client.close = AsyncMock()

        result = await _fetch_room_invites(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert result == []


@pytest.mark.anyio
async def test_fetch_room_invites_sync_failure() -> None:
    """Fetch room invites returns empty list on sync failure."""
    error_response = nio.SyncError(message="Sync failed")

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(return_value=error_response)
        mock_client.close = AsyncMock()

        result = await _fetch_room_invites(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert result == []


@pytest.mark.anyio
async def test_fetch_room_invites_exception() -> None:
    """Fetch room invites returns empty list on exception."""
    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(side_effect=Exception("Network error"))
        mock_client.close = AsyncMock()

        result = await _fetch_room_invites(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert result == []


@pytest.mark.anyio
async def test_fetch_room_invites_multiple() -> None:
    """Fetch room invites handles multiple invites."""
    rooms = FakeRooms(
        invite={
            "!room1:example.org": FakeInviteInfo([FakeInviteState(sender="@a:x")]),
            "!room2:example.org": FakeInviteInfo(
                [FakeInviteState(sender="@b:x", name="Room 2")]
            ),
            "!room3:example.org": FakeInviteInfo([]),
        }
    )

    sync_response = MagicMock(spec=nio.SyncResponse)
    sync_response.rooms = rooms

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(return_value=sync_response)
        mock_client.close = AsyncMock()

        result = await _fetch_room_invites(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert len(result) == 3


# --- _accept_room_invite tests ---


@pytest.mark.anyio
async def test_accept_room_invite_success() -> None:
    """Accept room invite returns True on success."""
    join_response = MagicMock(spec=nio.JoinResponse)

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.join = AsyncMock(return_value=join_response)
        mock_client.close = AsyncMock()

        result = await _accept_room_invite(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
            room_id="!room:example.org",
        )

    assert result is True
    mock_client.join.assert_called_once_with("!room:example.org")


@pytest.mark.anyio
async def test_accept_room_invite_failure() -> None:
    """Accept room invite returns False on failure response."""
    error_response = nio.JoinError(message="Cannot join")

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.join = AsyncMock(return_value=error_response)
        mock_client.close = AsyncMock()

        result = await _accept_room_invite(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
            room_id="!room:example.org",
        )

    assert result is False


@pytest.mark.anyio
async def test_accept_room_invite_exception() -> None:
    """Accept room invite returns False on exception."""
    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.join = AsyncMock(side_effect=Exception("Network error"))
        mock_client.close = AsyncMock()

        result = await _accept_room_invite(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
            room_id="!room:example.org",
        )

    assert result is False


# --- _send_confirmation tests ---


@pytest.mark.anyio
async def test_send_confirmation_success() -> None:
    """Send confirmation returns True on success."""
    send_response = MagicMock(spec=nio.RoomSendResponse)

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.room_send = AsyncMock(return_value=send_response)
        mock_client.close = AsyncMock()

        result = await _send_confirmation(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
            room_id="!room:example.org",
        )

    assert result is True
    mock_client.room_send.assert_called_once()


@pytest.mark.anyio
async def test_send_confirmation_failure() -> None:
    """Send confirmation returns False on failure response."""
    error_response = nio.RoomSendError(message="Cannot send")

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.room_send = AsyncMock(return_value=error_response)
        mock_client.close = AsyncMock()

        result = await _send_confirmation(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
            room_id="!room:example.org",
        )

    assert result is False


@pytest.mark.anyio
async def test_send_confirmation_exception() -> None:
    """Send confirmation returns False on exception."""
    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.room_send = AsyncMock(side_effect=Exception("Network error"))
        mock_client.close = AsyncMock()

        result = await _send_confirmation(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
            room_id="!room:example.org",
        )

    assert result is False


# --- _wait_for_room tests ---


class FakeTimeline:
    """Fake room timeline."""

    def __init__(self, events: list):
        self.events = events


class FakeRoomEvent:
    """Fake room event."""

    def __init__(self, sender: str):
        self.sender = sender


class FakeJoinInfo:
    """Fake joined room info."""

    def __init__(self, events: list):
        self.timeline = FakeTimeline(events)


class FakeJoinRooms:
    """Fake rooms with join info."""

    def __init__(self, join: dict):
        self.join = join
        self.invite = {}


def _make_sync_response(rooms) -> MagicMock:
    """Create a mock SyncResponse that passes isinstance check."""
    response = MagicMock(spec=nio.SyncResponse)
    response.rooms = rooms
    response.next_batch = "batch1"
    return response


@pytest.mark.anyio
async def test_wait_for_room_receives_message() -> None:
    """Wait for room returns room_id when message received."""
    # Initial sync with no messages
    initial_rooms = FakeJoinRooms(join={})
    initial_response = _make_sync_response(initial_rooms)

    # Second sync with a message from another user
    join_info = FakeJoinInfo([FakeRoomEvent(sender="@user:example.org")])
    message_rooms = FakeJoinRooms(join={"!room:example.org": join_info})
    message_response = _make_sync_response(message_rooms)
    message_response.next_batch = "batch2"

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(side_effect=[initial_response, message_response])
        mock_client.close = AsyncMock()

        result = await _wait_for_room(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert result == "!room:example.org"


@pytest.mark.anyio
async def test_wait_for_room_ignores_own_messages() -> None:
    """Wait for room ignores messages from own user."""
    # Initial sync
    initial_rooms = FakeJoinRooms(join={})
    initial_response = _make_sync_response(initial_rooms)

    # Second sync with own message (should be ignored)
    own_join_info = FakeJoinInfo([FakeRoomEvent(sender="@bot:example.org")])
    own_message_rooms = FakeJoinRooms(join={"!room:example.org": own_join_info})
    own_response = _make_sync_response(own_message_rooms)
    own_response.next_batch = "batch2"

    # Third sync with other user message
    other_join_info = FakeJoinInfo([FakeRoomEvent(sender="@user:example.org")])
    other_message_rooms = FakeJoinRooms(join={"!room2:example.org": other_join_info})
    other_response = _make_sync_response(other_message_rooms)
    other_response.next_batch = "batch3"

    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(
            side_effect=[initial_response, own_response, other_response]
        )
        mock_client.close = AsyncMock()

        result = await _wait_for_room(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert result == "!room2:example.org"


@pytest.mark.anyio
async def test_wait_for_room_exception() -> None:
    """Wait for room returns None on exception."""
    with patch("takopi_matrix.onboarding.rooms.nio.AsyncClient") as MockClient:
        mock_client = AsyncMock()
        MockClient.return_value = mock_client
        mock_client.sync = AsyncMock(side_effect=Exception("Network error"))
        mock_client.close = AsyncMock()

        result = await _wait_for_room(
            homeserver="https://matrix.example.org",
            user_id="@bot:example.org",
            access_token="token123",
        )

    assert result is None
