"""Matrix client package - protocol, outbox, and client implementation."""

from .protocol import (
    SEND_PRIORITY,
    DELETE_PRIORITY,
    EDIT_PRIORITY,
    TYPING_PRIORITY,
    RetryAfter,
    MatrixRetryAfter,
    NioClientProtocol,
)
from .outbox import OutboxOp, MatrixOutbox
from .parsers import (
    parse_matrix_error,
    parse_room_message,
    parse_room_media,
    parse_room_audio,
    parse_reaction,
)
from .content_builders import _build_reply_content, _build_edit_content
from .client import MatrixClient

__all__ = [
    # Priority constants
    "SEND_PRIORITY",
    "DELETE_PRIORITY",
    "EDIT_PRIORITY",
    "TYPING_PRIORITY",
    # Exceptions
    "RetryAfter",
    "MatrixRetryAfter",
    # Protocol
    "NioClientProtocol",
    # Outbox
    "OutboxOp",
    "MatrixOutbox",
    # Parsers
    "parse_matrix_error",
    "parse_room_message",
    "parse_room_media",
    "parse_room_audio",
    "parse_reaction",
    # Content builders (PUBLIC + PRIVATE for tests)
    "_build_reply_content",  # Used by tests
    "_build_edit_content",  # Used by tests
    # Client
    "MatrixClient",
]
