"""Cancel handling for commands and reactions."""

from __future__ import annotations

from takopi.api import MessageRef, RenderedMessage, RunningTasks, SendOptions
from takopi.logging import get_logger

from ..types import MatrixIncomingMessage, MatrixReaction
from .config import MatrixBridgeConfig

logger = get_logger(__name__)

# Cancel reaction emojis
_CANCEL_REACTIONS = frozenset({"\u274c", "x", "X"})


def _is_cancel_command(text: str) -> bool:
    """Check if text is a cancel command."""
    stripped = text.strip()
    if not stripped:
        return False
    command = stripped.split(maxsplit=1)[0]
    return command == "/cancel" or command.startswith("/cancel@")


async def _send_plain(
    cfg: MatrixBridgeConfig,
    *,
    room_id: str,
    reply_to_event_id: str,
    text: str,
) -> None:
    """Send a plain text message as a reply."""
    reply_to = MessageRef(channel_id=room_id, message_id=reply_to_event_id)
    await cfg.exec_cfg.transport.send(
        channel_id=room_id,
        message=RenderedMessage(text=text),
        options=SendOptions(reply_to=reply_to, notify=True),
    )


async def _handle_cancel(
    cfg: MatrixBridgeConfig,
    msg: MatrixIncomingMessage,
    running_tasks: RunningTasks,
) -> None:
    """Handle /cancel command."""
    room_id = msg.room_id
    event_id = msg.event_id
    reply_to = msg.reply_to_event_id

    if reply_to is None:
        if msg.reply_to_text:
            await _send_plain(
                cfg,
                room_id=room_id,
                reply_to_event_id=event_id,
                text="nothing is currently running for that message.",
            )
            return
        await _send_plain(
            cfg,
            room_id=room_id,
            reply_to_event_id=event_id,
            text="reply to the progress message to cancel.",
        )
        return

    progress_ref = MessageRef(channel_id=room_id, message_id=reply_to)
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        await _send_plain(
            cfg,
            room_id=room_id,
            reply_to_event_id=event_id,
            text="nothing is currently running for that message.",
        )
        return

    logger.info(
        "cancel.requested",
        room_id=room_id,
        progress_event_id=reply_to,
    )
    running_task.cancel_requested.set()


async def _handle_cancel_reaction(
    cfg: MatrixBridgeConfig,
    reaction: MatrixReaction,
    running_tasks: RunningTasks,
) -> None:
    """Handle cancel reaction on a progress message."""
    if reaction.key not in _CANCEL_REACTIONS:
        return

    progress_ref = MessageRef(
        channel_id=reaction.room_id,
        message_id=reaction.target_event_id,
    )
    running_task = running_tasks.get(progress_ref)
    if running_task is None:
        return

    logger.info(
        "cancel.reaction",
        room_id=reaction.room_id,
        target_event_id=reaction.target_event_id,
        sender=reaction.sender,
    )
    running_task.cancel_requested.set()
