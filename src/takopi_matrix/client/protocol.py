"""Matrix protocol definitions, exceptions, and priority constants."""

from __future__ import annotations

from typing import (
    Any,
    Protocol,
)


# Priority constants for outbox ordering
SEND_PRIORITY = 0
DELETE_PRIORITY = 1
EDIT_PRIORITY = 2
TYPING_PRIORITY = 3


class RetryAfter(Exception):
    """Exception indicating an operation should be retried after a delay."""

    def __init__(self, retry_after: float, description: str | None = None) -> None:
        super().__init__(description or f"retry after {retry_after}")
        self.retry_after = float(retry_after)
        self.description = description


class MatrixRetryAfter(RetryAfter):
    """Matrix-specific retry after exception."""

    pass


class NioClientProtocol(Protocol):
    """Protocol for matrix-nio AsyncClient compatibility."""

    user_id: str
    access_token: str  # Writable property for token-based login
    device_id: str  # Writable property for device identification

    async def close(self) -> None: ...

    async def login(
        self, password: str | None = None, device_name: str | None = None
    ) -> Any: ...

    async def sync(
        self,
        timeout: int = 30000,
        sync_filter: dict[str, Any] | None = None,
        since: str | None = None,
        full_state: bool = False,
    ) -> Any: ...

    async def room_send(
        self,
        room_id: str,
        message_type: str,
        content: dict[str, Any],
        tx_id: str | None = None,
        ignore_unverified_devices: bool = True,
    ) -> Any: ...

    async def room_redact(
        self,
        room_id: str,
        event_id: str,
        reason: str | None = None,
        tx_id: str | None = None,
    ) -> Any: ...

    async def room_typing(
        self,
        room_id: str,
        typing_state: bool = True,
        timeout: int = 30000,
    ) -> Any: ...

    async def room_read_markers(
        self,
        room_id: str,
        fully_read_event: str,
        read_event: str | None = None,
    ) -> Any: ...

    async def download(
        self,
        mxc: str,
        filename: str | None = None,
        allow_remote: bool = True,
    ) -> Any: ...

    async def room_get_event(self, room_id: str, event_id: str) -> Any: ...

    async def join(self, room_id: str) -> Any: ...

    # E2EE methods (optional, called conditionally with getattr)
    async def keys_upload(self) -> Any: ...

    async def keys_claim(self, users: dict[str, Any]) -> Any: ...
