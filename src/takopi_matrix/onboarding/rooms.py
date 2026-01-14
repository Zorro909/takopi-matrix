"""Room invite and acceptance handling."""

from __future__ import annotations

from dataclasses import dataclass

import anyio
import nio


@dataclass(frozen=True, slots=True)
class RoomInvite:
    """A pending room invite."""

    room_id: str
    inviter: str | None
    room_name: str | None


async def _fetch_room_invites(
    homeserver: str,
    user_id: str,
    access_token: str,
) -> list[RoomInvite]:
    """Fetch pending room invites."""
    client = nio.AsyncClient(homeserver, user_id)
    client.access_token = access_token
    client.user_id = user_id

    try:
        response = await client.sync(timeout=5000)
        if not isinstance(response, nio.SyncResponse):
            return []

        invites: list[RoomInvite] = []
        for room_id, invite_info in response.rooms.invite.items():
            inviter: str | None = None
            room_name: str | None = None

            for event in invite_info.invite_state:
                if hasattr(event, "sender"):
                    inviter = event.sender
                if hasattr(event, "name"):
                    room_name = event.name

            invites.append(RoomInvite(room_id, inviter, room_name))

        return invites
    except Exception:
        return []
    finally:
        await client.close()


async def _accept_room_invite(
    homeserver: str,
    user_id: str,
    access_token: str,
    room_id: str,
) -> bool:
    """Accept a room invite."""
    client = nio.AsyncClient(homeserver, user_id)
    client.access_token = access_token
    client.user_id = user_id

    try:
        response = await client.join(room_id)
        return isinstance(response, nio.JoinResponse)
    except Exception:
        return False
    finally:
        await client.close()


async def _wait_for_room(
    homeserver: str,
    user_id: str,
    access_token: str,
) -> str | None:
    """Wait for a message in any room and return the room_id."""
    client = nio.AsyncClient(homeserver, user_id)
    client.access_token = access_token
    client.user_id = user_id

    try:
        since: str | None = None
        initial = await client.sync(timeout=0)
        if isinstance(initial, nio.SyncResponse):
            since = initial.next_batch

        while True:
            response = await client.sync(timeout=30000, since=since)
            if not isinstance(response, nio.SyncResponse):
                await anyio.sleep(1)
                continue

            since = response.next_batch

            for room_id, room_info in response.rooms.join.items():
                for event in room_info.timeline.events:
                    if hasattr(event, "sender") and event.sender != user_id:
                        return room_id

    except Exception:
        return None
    finally:
        await client.close()


async def _send_confirmation(
    homeserver: str,
    user_id: str,
    access_token: str,
    room_id: str,
) -> bool:
    """Send confirmation message to room."""
    client = nio.AsyncClient(homeserver, user_id)
    client.access_token = access_token
    client.user_id = user_id

    try:
        response = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": "takopi is configured and ready.",
            },
        )
        return isinstance(response, nio.RoomSendResponse)
    except Exception:
        return False
    finally:
        await client.close()
