"""Trigger mode for Matrix transport.

Controls when the bot responds to messages:
- 'all': Respond to all messages in allowed rooms (default)
- 'mentions': Only respond when mentioned by display name or user ID
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from takopi.api import TransportRuntime

from .bridge.commands.parse import parse_slash_command

if TYPE_CHECKING:
    from .room_prefs import RoomPrefsStore
    from .thread_state import MatrixThreadStateStore

TriggerMode = Literal["all", "mentions"]


async def resolve_trigger_mode(
    *,
    room_id: str,
    room_prefs: RoomPrefsStore | None,
    thread_root_event_id: str | None = None,
    thread_state: MatrixThreadStateStore | None = None,
) -> TriggerMode:
    """Resolve the effective trigger mode for a room.

    Args:
        room_id: The Matrix room ID.
        room_prefs: The room preferences store (if available).

    Returns:
        The effective trigger mode ('all' or 'mentions').
    """
    if (
        thread_state is not None
        and thread_root_event_id is not None
        and thread_root_event_id.strip()
    ):
        thread_mode = await thread_state.get_trigger_mode(room_id, thread_root_event_id)
        if thread_mode == "mentions":
            return "mentions"

    if room_prefs is not None:
        room_mode = await room_prefs.get_trigger_mode(room_id)
        if room_mode == "mentions":
            return "mentions"
    return "all"


def should_trigger_run(
    text: str,
    *,
    own_user_id: str,
    own_display_name: str | None,
    reply_to_is_bot: bool,
    runtime: TransportRuntime,
    command_ids: set[str],
    reserved_room_commands: set[str],
) -> bool:
    """Check if a message should trigger a bot response in mentions mode.

    In 'mentions' mode, the bot only responds when:
    1. The message contains @user_id mention
    2. The message contains display name mention
    3. The message is a reply to the bot's message
    4. The message is a slash command (reserved or plugin)
    5. The message is directed at an engine/project alias

    Args:
        text: The message text.
        own_user_id: The bot's Matrix user ID (e.g., '@takopi:matrix.org').
        own_display_name: The bot's display name (e.g., 'Takopi Bot').
        reply_to_is_bot: Whether this message is a reply to a bot message.
        runtime: The transport runtime for engine/project lookup.
        command_ids: Set of known plugin command IDs.
        reserved_room_commands: Set of reserved room command IDs.

    Returns:
        True if the message should trigger a bot response.
    """
    lowered = text.lower()

    # Check for @user_id mention (Matrix style: @user:server)
    if own_user_id and own_user_id.lower() in lowered:
        return True

    # Check for display name mention
    if own_display_name:
        display_lower = own_display_name.lower()
        if display_lower in lowered:
            return True

    # Reply to bot's message
    if reply_to_is_bot:
        return True

    # Check for slash commands
    command_id, _ = parse_slash_command(text)
    if not command_id:
        return False

    # Reserved commands (cancel, etc.) and plugin commands
    if command_id in reserved_room_commands or command_id in command_ids:
        return True

    # Engine IDs (codex, claude, etc.)
    engine_ids = {engine.lower() for engine in runtime.available_engine_ids()}
    if command_id in engine_ids:
        return True

    # Project aliases
    project_aliases = {alias.lower() for alias in runtime.project_aliases()}
    return command_id in project_aliases
