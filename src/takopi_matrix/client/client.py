"""Matrix client with outbox-based rate limiting."""

from __future__ import annotations

import functools
import itertools
import json
import time
from pathlib import Path
from typing import (
    Any,
)
from collections.abc import Awaitable, Callable, Hashable

import anyio
import httpx
import nio

from takopi.logging import get_logger

from .protocol import (
    NioClientProtocol,
    MatrixRetryAfter,
    SEND_PRIORITY,
    DELETE_PRIORITY,
    EDIT_PRIORITY,
    TYPING_PRIORITY,
)
from .outbox import OutboxOp, MatrixOutbox
from .content_builders import _build_reply_content, _build_edit_content

logger = get_logger(__name__)


def _require_login(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """
    Decorator ensuring Matrix client is logged in before executing method.

    Wraps async methods to:
    1. Ensure nio client is initialized
    2. Check if logged in
    3. Auto-login if not logged in
    4. Return None if login fails
    5. Execute method if logged in

    Usage:
        @_require_login
        async def send_message(self, ...):
            # No need for login boilerplate
            ...
    """

    @functools.wraps(func)
    async def wrapper(self: MatrixClient, *args: Any, **kwargs: Any) -> Any:
        await self._ensure_nio_client()
        if not self._logged_in and not await self.login():
            return None
        return await func(self, *args, **kwargs)

    return wrapper


class MatrixClient:
    """Matrix client with outbox-based rate limiting."""

    def __init__(
        self,
        homeserver: str,
        user_id: str,
        *,
        access_token: str | None = None,
        password: str | None = None,
        device_id: str | None = None,
        device_name: str = "Takopi",
        crypto_store_path: Path | None = None,
        sync_store_path: Path | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        interval: float = 0.1,
    ) -> None:
        self.homeserver = homeserver.rstrip("/")
        self.user_id = user_id
        self._access_token = access_token
        self._password = password
        self._device_id = device_id
        self._device_name = device_name
        self._crypto_store_path = crypto_store_path
        self._sync_store_path = sync_store_path or self._default_sync_store_path()
        self._http_client = http_client or httpx.AsyncClient(timeout=120)
        self._owns_http_client = http_client is None
        self._clock = clock
        self._sleep = sleep
        self._nio_client: NioClientProtocol | None = None
        self._logged_in = False
        self._sync_token: str | None = self._load_sync_token()

        self._outbox = MatrixOutbox(
            interval=interval,
            clock=clock,
            sleep=sleep,
            on_error=self.log_request_error,
            on_outbox_error=self.log_outbox_failure,
        )
        self._seq = itertools.count()

    @property
    def e2ee_available(self) -> bool:
        """Check if E2EE dependencies are available."""
        return hasattr(nio, "crypto")

    def _default_sync_store_path(self) -> Path:
        """Get the default path for storing the sync token."""
        return Path.home() / ".takopi" / "matrix_sync.json"

    def _load_sync_token(self) -> str | None:
        """Load the sync token from disk if available."""
        if self._sync_store_path is None:
            return None
        try:
            if self._sync_store_path.exists():
                data = json.loads(self._sync_store_path.read_text())
                if data.get("user_id") == self.user_id:
                    token = data.get("next_batch")
                    if token:
                        logger.debug(
                            "matrix.sync.token_loaded",
                            user_id=self.user_id,
                        )
                    return token
        except Exception as exc:
            logger.warning(
                "matrix.sync.token_load_failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
        return None

    def _save_sync_token(self, token: str) -> None:
        """Save the sync token to disk."""
        if self._sync_store_path is None:
            return
        try:
            self._sync_store_path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_store_path.write_text(
                json.dumps({"next_batch": token, "user_id": self.user_id})
            )
        except Exception as exc:
            logger.warning(
                "matrix.sync.token_save_failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )

    def log_request_error(self, request: OutboxOp, exc: Exception) -> None:
        logger.error(
            "matrix.outbox.request_failed",
            method=request.label,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    def log_outbox_failure(self, exc: Exception) -> None:
        logger.error(
            "matrix.outbox.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )

    def unique_key(self, prefix: str) -> tuple[str, int]:
        return (prefix, next(self._seq))

    async def enqueue_op(
        self,
        *,
        key: Hashable,
        label: str,
        execute: Callable[[], Awaitable[Any]],
        priority: int,
        room_id: str | None,
        wait: bool = True,
    ) -> Any:
        request = OutboxOp(
            execute=execute,
            priority=priority,
            queued_at=0.0,
            updated_at=self._clock(),
            room_id=room_id,
            label=label,
        )
        return await self._outbox.enqueue(key=key, op=request, wait=wait)

    async def drop_pending_edits(self, *, room_id: str, event_id: str) -> None:
        await self._outbox.drop_pending(key=("edit", room_id, event_id))

    async def _ensure_nio_client(self) -> NioClientProtocol:
        """Lazily initialize the nio client."""
        if self._nio_client is not None:
            return self._nio_client

        store_path = self._crypto_store_path
        if store_path is not None and self.e2ee_available:
            store_path.parent.mkdir(parents=True, exist_ok=True)
            config = nio.AsyncClientConfig(
                store_sync_tokens=True,
                encryption_enabled=True,
            )
            # nio.AsyncClient implements NioClientProtocol structurally
            # Cast required due to type checker limitations with Protocol
            from typing import cast

            self._nio_client = cast(
                NioClientProtocol,
                nio.AsyncClient(
                    self.homeserver,
                    self.user_id,
                    device_id=self._device_id,
                    store_path=str(store_path.parent),
                    config=config,
                ),
            )
        else:
            # nio.AsyncClient implements NioClientProtocol structurally
            # Cast required due to type checker limitations with Protocol
            from typing import cast

            self._nio_client = cast(
                NioClientProtocol,
                nio.AsyncClient(
                    self.homeserver,
                    self.user_id,
                    device_id=self._device_id,
                ),
            )

        # _nio_client is guaranteed to be set by this point
        return self._nio_client

    async def login(self) -> bool:
        """Login to the homeserver."""
        client = await self._ensure_nio_client()

        if self._access_token:
            client.access_token = self._access_token
            client.user_id = self.user_id
            if self._device_id:
                client.device_id = self._device_id
            self._logged_in = True
            logger.info("matrix.login.token", user_id=self.user_id)
            return True

        if self._password:
            response = await client.login(
                password=self._password,
                device_name=self._device_name,
            )
            if isinstance(response, nio.LoginResponse):
                self._access_token = response.access_token
                self._device_id = response.device_id
                self._logged_in = True
                logger.info(
                    "matrix.login.password",
                    user_id=self.user_id,
                    device_id=response.device_id,
                )
                return True
            logger.error(
                "matrix.login.failed",
                error=getattr(response, "message", str(response)),
            )
            return False

        logger.error("matrix.login.no_credentials")
        return False

    @_require_login
    async def sync(
        self,
        timeout_ms: int = 30000,
        full_state: bool = False,
    ) -> Any:
        """Perform a sync with the homeserver."""
        client = self._nio_client
        assert client is not None  # Guaranteed by @_require_login

        try:
            response = await client.sync(
                timeout=timeout_ms,
                since=self._sync_token,
                full_state=full_state,
            )
            if isinstance(response, nio.SyncResponse):
                self._sync_token = response.next_batch
                self._save_sync_token(self._sync_token)
                return response
            if hasattr(response, "retry_after_ms"):
                retry_ms = getattr(response, "retry_after_ms", 5000)
                raise MatrixRetryAfter(retry_ms / 1000.0)
            logger.error(
                "matrix.sync.failed",
                error=getattr(response, "message", str(response)),
            )
            return None
        except Exception as exc:
            if isinstance(exc, MatrixRetryAfter):
                raise
            logger.error(
                "matrix.sync.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    async def init_e2ee(self) -> bool:
        """
        Initialize E2EE after login - load store and upload device keys.

        Should be called after successful login to enable encryption.
        Returns True if E2EE was initialized successfully.
        """
        if not self.e2ee_available:
            return False

        client = await self._ensure_nio_client()

        try:
            # Load the crypto store (creates Olm account if needed)
            load_store_fn = getattr(client, "load_store", None)
            if load_store_fn is not None:
                load_store_fn()
                logger.debug("matrix.e2ee.store_loaded")

            # Upload device keys if needed
            should_upload = getattr(client, "should_upload_keys", False)
            if should_upload:
                response = await client.keys_upload()
                if isinstance(response, nio.KeysUploadError):
                    logger.error(
                        "matrix.e2ee.keys_upload_failed",
                        error=response.message,
                    )
                    return False
                logger.info("matrix.e2ee.keys_uploaded")

            return True
        except Exception as exc:
            logger.error(
                "matrix.e2ee.init_failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return False

    async def ensure_room_keys(self, room_id: str) -> None:
        """
        Ensure we have encryption sessions with all devices in a room.

        This claims one-time keys and shares the room session so we can
        send encrypted messages.
        """
        if not self.e2ee_available:
            return

        client = await self._ensure_nio_client()

        try:
            # Claim one-time keys from devices we don't have sessions with
            should_claim = getattr(client, "should_claim_keys", False)
            if should_claim:
                get_users_fn = getattr(client, "get_users_for_key_claiming", None)
                if get_users_fn is not None:
                    users = get_users_fn()
                    if users:
                        response = await client.keys_claim(users)
                        if isinstance(response, nio.KeysClaimResponse):
                            logger.debug(
                                "matrix.e2ee.keys_claimed",
                                room_id=room_id,
                                users=list(users.keys()),
                            )

            # Share group session with room members
            share_fn = getattr(client, "share_group_session", None)
            if share_fn is not None:
                response = await share_fn(
                    room_id,
                    ignore_unverified_devices=True,
                )
                if isinstance(response, nio.ShareGroupSessionError):
                    logger.debug(
                        "matrix.e2ee.share_session_failed",
                        room_id=room_id,
                        error=response.message,
                    )
                else:
                    logger.debug("matrix.e2ee.session_shared", room_id=room_id)

        except Exception as exc:
            logger.debug(
                "matrix.e2ee.ensure_keys_error",
                room_id=room_id,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )

    async def trust_room_devices(self, room_id: str) -> None:
        """
        Auto-trust all devices in a room for E2EE.

        This enables encryption to work without manual device verification.
        """
        if not self.e2ee_available:
            return

        # nio guaranteed available by __init__ validation

        client = await self._ensure_nio_client()

        try:
            rooms = getattr(client, "rooms", {})
            room = rooms.get(room_id)
            if room is None:
                return

            users = getattr(room, "users", {})
            device_store = getattr(client, "device_store", {})

            for user_id in users:
                devices = device_store.get(user_id, {})
                for device_id, device in devices.items():
                    if not getattr(device, "verified", False):
                        verify_fn = getattr(client, "verify_device", None)
                        if verify_fn is not None:
                            verify_fn(device)
                            logger.debug(
                                "matrix.e2ee.device_trusted",
                                user_id=user_id,
                                device_id=device_id,
                            )
        except Exception as exc:
            logger.debug(
                "matrix.e2ee.trust_error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )

    async def _request_room_key(self, event: Any) -> None:
        """Request room key for an undecrypted event (best effort)."""
        try:
            client = await self._ensure_nio_client()
            request_fn = getattr(client, "request_room_key", None)
            if request_fn is not None:
                await request_fn(event)
                logger.debug(
                    "matrix.e2ee.key_requested",
                    event_id=getattr(event, "event_id", None),
                    sender=getattr(event, "sender", None),
                )
        except Exception:
            pass  # Best effort - don't fail on key request errors

    async def decrypt_event(self, event: Any) -> Any | None:
        """
        Attempt to decrypt an encrypted room event.

        Returns the decrypted event if successful, None if decryption fails
        or E2EE is not available. On failure, automatically requests the
        missing room key.
        """
        if not self.e2ee_available:
            return None

        client = await self._ensure_nio_client()

        # Only MegolmEvent can be decrypted
        if not isinstance(event, nio.MegolmEvent):
            return None

        try:
            # decrypt_event is available on nio.AsyncClient with E2EE support
            decrypt_fn = getattr(client, "decrypt_event", None)
            if decrypt_fn is None:
                return None
            decrypted = decrypt_fn(event)
            if isinstance(decrypted, (nio.BadEvent, nio.UnknownBadEvent)):
                logger.debug(
                    "matrix.decrypt.failed",
                    event_id=getattr(event, "event_id", None),
                    sender=getattr(event, "sender", None),
                    error=getattr(decrypted, "reason", "unknown"),
                )
                # Request the missing key
                await self._request_room_key(event)
                return None
            return decrypted
        except Exception as exc:
            logger.debug(
                "matrix.decrypt.error",
                event_id=getattr(event, "event_id", None),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            # Request the missing key on exception too
            await self._request_room_key(event)
            return None

    async def send_message(
        self,
        room_id: str,
        body: str,
        *,
        formatted_body: str | None = None,
        reply_to_event_id: str | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any] | None:
        """Send a message to a room."""

        async def execute() -> dict[str, Any] | None:
            await self._ensure_nio_client()
            if not self._logged_in and not await self.login():
                return None
            client = self._nio_client
            assert client is not None

            if reply_to_event_id:
                content = _build_reply_content(body, formatted_body, reply_to_event_id)
            else:
                content: dict[str, Any] = {
                    "msgtype": "m.text",
                    "body": body,
                }
                if formatted_body:
                    content["format"] = "org.matrix.custom.html"
                    content["formatted_body"] = formatted_body

            try:
                response = await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                    ignore_unverified_devices=True,
                )
                if isinstance(response, nio.RoomSendResponse):
                    return {"event_id": response.event_id, "room_id": room_id}
                if hasattr(response, "retry_after_ms"):
                    retry_ms = getattr(response, "retry_after_ms", 5000)
                    raise MatrixRetryAfter(retry_ms / 1000.0)
                logger.error(
                    "matrix.send.failed",
                    room_id=room_id,
                    error=getattr(response, "message", str(response)),
                )
                return None
            except MatrixRetryAfter:
                raise
            except Exception as exc:
                logger.error(
                    "matrix.send.error",
                    room_id=room_id,
                    error=str(exc),
                    error_type=exc.__class__.__name__,
                )
                return None

        return await self.enqueue_op(
            key=self.unique_key("send"),
            label="send_message",
            execute=execute,
            priority=SEND_PRIORITY,
            room_id=room_id,
        )

    async def edit_message(
        self,
        room_id: str,
        event_id: str,
        body: str,
        *,
        formatted_body: str | None = None,
        wait: bool = True,
    ) -> dict[str, Any] | None:
        """Edit an existing message using m.replace."""

        async def execute() -> dict[str, Any] | None:
            await self._ensure_nio_client()
            if not self._logged_in and not await self.login():
                return None
            client = self._nio_client
            assert client is not None

            content = _build_edit_content(body, formatted_body, event_id)

            try:
                response = await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                    ignore_unverified_devices=True,
                )
                if isinstance(response, nio.RoomSendResponse):
                    return {"event_id": response.event_id, "room_id": room_id}
                if hasattr(response, "retry_after_ms"):
                    retry_ms = getattr(response, "retry_after_ms", 5000)
                    raise MatrixRetryAfter(retry_ms / 1000.0)
                logger.error(
                    "matrix.edit.failed",
                    room_id=room_id,
                    event_id=event_id,
                    error=getattr(response, "message", str(response)),
                )
                return None
            except MatrixRetryAfter:
                raise
            except Exception as exc:
                logger.error(
                    "matrix.edit.error",
                    room_id=room_id,
                    event_id=event_id,
                    error=str(exc),
                    error_type=exc.__class__.__name__,
                )
                return None

        return await self.enqueue_op(
            key=("edit", room_id, event_id),
            label="edit_message",
            execute=execute,
            priority=EDIT_PRIORITY,
            room_id=room_id,
            wait=wait,
        )

    async def redact_message(
        self,
        room_id: str,
        event_id: str,
        reason: str | None = None,
    ) -> bool:
        """Redact (delete) a message."""
        await self.drop_pending_edits(room_id=room_id, event_id=event_id)

        async def execute() -> bool:
            await self._ensure_nio_client()
            if not self._logged_in and not await self.login():
                return False
            client = self._nio_client
            assert client is not None

            try:
                response = await client.room_redact(
                    room_id=room_id,
                    event_id=event_id,
                    reason=reason,
                )
                if isinstance(response, nio.RoomRedactResponse):
                    return True
                if hasattr(response, "retry_after_ms"):
                    retry_ms = getattr(response, "retry_after_ms", 5000)
                    raise MatrixRetryAfter(retry_ms / 1000.0)
                logger.error(
                    "matrix.redact.failed",
                    room_id=room_id,
                    event_id=event_id,
                    error=getattr(response, "message", str(response)),
                )
                return False
            except MatrixRetryAfter:
                raise
            except Exception as exc:
                logger.error(
                    "matrix.redact.error",
                    room_id=room_id,
                    event_id=event_id,
                    error=str(exc),
                    error_type=exc.__class__.__name__,
                )
                return False

        return bool(
            await self.enqueue_op(
                key=("redact", room_id, event_id),
                label="redact_message",
                execute=execute,
                priority=DELETE_PRIORITY,
                room_id=room_id,
            )
        )

    async def send_typing(
        self,
        room_id: str,
        typing: bool = True,
        timeout_ms: int = 30000,
    ) -> bool:
        """Send typing indicator to a room."""

        async def execute() -> bool:
            await self._ensure_nio_client()
            if not self._logged_in and not await self.login():
                return False
            client = self._nio_client
            assert client is not None

            try:
                response = await client.room_typing(
                    room_id=room_id,
                    typing_state=typing,
                    timeout=timeout_ms,
                )
                return bool(isinstance(response, nio.RoomTypingResponse))
            except Exception as exc:
                logger.debug(
                    "matrix.typing.error",
                    room_id=room_id,
                    error=str(exc),
                )
                return False

        return bool(
            await self.enqueue_op(
                key=("typing", room_id),
                label="send_typing",
                execute=execute,
                priority=TYPING_PRIORITY,
                room_id=room_id,
                wait=False,
            )
        )

    async def send_read_receipt(
        self,
        room_id: str,
        event_id: str,
    ) -> bool:
        """Send read receipt for an event."""

        async def execute() -> bool:
            await self._ensure_nio_client()
            if not self._logged_in and not await self.login():
                return False
            client = self._nio_client
            assert client is not None

            try:
                response = await client.room_read_markers(
                    room_id=room_id,
                    fully_read_event=event_id,
                    read_event=event_id,
                )
                return bool(isinstance(response, nio.RoomReadMarkersResponse))
            except Exception as exc:
                logger.debug(
                    "matrix.read_receipt.error",
                    room_id=room_id,
                    event_id=event_id,
                    error=str(exc),
                )
                return False

        return bool(
            await self.enqueue_op(
                key=self.unique_key("read_receipt"),
                label="send_read_receipt",
                execute=execute,
                priority=TYPING_PRIORITY,
                room_id=room_id,
                wait=False,
            )
        )

    @_require_login
    async def download_file(
        self,
        mxc_url: str,
        *,
        max_size: int = 50 * 1024 * 1024,
        file_info: dict[str, Any] | None = None,
    ) -> bytes | None:
        """
        Download a file from Matrix media repository.

        If file_info is provided (for encrypted files), the downloaded
        content will be decrypted using the key/iv/hash from file_info.
        """
        client = self._nio_client
        assert client is not None  # Guaranteed by @_require_login

        try:
            response = await client.download(mxc=mxc_url)
            if isinstance(response, nio.DownloadResponse):
                content = response.body
                if len(content) > max_size:
                    logger.warning(
                        "matrix.download.too_large",
                        mxc_url=mxc_url,
                        size=len(content),
                        max_size=max_size,
                    )
                    return None

                # Decrypt if file_info contains encryption keys
                if file_info and self.e2ee_available:
                    decrypted = self._decrypt_attachment(content, file_info)
                    if decrypted is not None:
                        return decrypted
                    # Fall through to return encrypted content if decryption fails
                    logger.warning(
                        "matrix.download.decryption_failed",
                        mxc_url=mxc_url,
                    )

                return content
            logger.error(
                "matrix.download.failed",
                mxc_url=mxc_url,
                error=getattr(response, "message", str(response)),
            )
            return None
        except Exception as exc:
            logger.error(
                "matrix.download.error",
                mxc_url=mxc_url,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    def _decrypt_attachment(
        self,
        data: bytes,
        file_info: dict[str, Any],
    ) -> bytes | None:
        """Decrypt an encrypted attachment using file_info keys."""
        try:
            from nio.crypto.attachments import decrypt_attachment
        except ImportError:
            return None

        try:
            key = file_info.get("key", {})
            key_k = key.get("k") if isinstance(key, dict) else None
            hashes = file_info.get("hashes", {})
            hash_sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
            iv = file_info.get("iv")

            if not all([key_k, hash_sha256, iv]):
                logger.debug(
                    "matrix.decrypt_attachment.missing_keys",
                    has_key=bool(key_k),
                    has_hash=bool(hash_sha256),
                    has_iv=bool(iv),
                )
                return None

            # Type narrowing: assert types after None check
            assert isinstance(key_k, str), "key_k must be str"
            assert isinstance(hash_sha256, str), "hash_sha256 must be str"
            assert isinstance(iv, str), "iv must be str"

            decrypted = decrypt_attachment(data, key_k, hash_sha256, iv)
            return decrypted
        except Exception as exc:
            logger.debug(
                "matrix.decrypt_attachment.error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    @_require_login
    async def join_room(self, room_id: str) -> bool:
        """Join a room."""
        client = self._nio_client
        assert client is not None  # Guaranteed by @_require_login

        try:
            response = await client.join(room_id)
            if isinstance(response, nio.JoinResponse):
                logger.info("matrix.join.success", room_id=room_id)
                return True
            logger.error(
                "matrix.join.failed",
                room_id=room_id,
                error=getattr(response, "message", str(response)),
            )
            return False
        except Exception as exc:
            logger.error(
                "matrix.join.error",
                room_id=room_id,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return False

    @_require_login
    async def get_event_text(self, room_id: str, event_id: str) -> str | None:
        """Fetch the text body of a specific event.

        Returns the text/body content of the event, or None if:
        - The event doesn't exist
        - The event is not a text message
        - An error occurs during fetching
        """
        client = self._nio_client
        assert client is not None  # Guaranteed by @_require_login

        try:
            logger.debug(
                "matrix.get_event.fetching",
                room_id=room_id,
                event_id=event_id,
            )
            response = await client.room_get_event(room_id, event_id)

            logger.debug(
                "matrix.get_event.response_type",
                response_type=type(response).__name__,
                has_event=hasattr(response, "event"),
            )

            # Check for error responses
            if hasattr(response, "status_code") and response.status_code != "200":
                logger.debug(
                    "matrix.get_event.failed",
                    room_id=room_id,
                    event_id=event_id,
                    status=getattr(response, "status_code", None),
                )
                return None

            # Extract event from response
            event = getattr(response, "event", None)
            if event is None:
                logger.debug("matrix.get_event.no_event_in_response")
                return None

            # nio returns Event objects, not dicts
            # Try to get body from the Event object's source dict or attributes
            body = None

            # First try: Event object has a 'source' attribute with raw dict
            source = getattr(event, "source", None)
            if isinstance(source, dict):
                content = source.get("content")
                if isinstance(content, dict):
                    body = content.get("body")

            # Second try: Event object might have body as direct attribute
            if body is None:
                body = getattr(event, "body", None)

            logger.debug(
                "matrix.get_event.extraction",
                has_source=source is not None,
                source_is_dict=isinstance(source, dict),
                body_found=body is not None,
                body_type=type(body).__name__ if body is not None else "None",
            )

            if isinstance(body, str):
                logger.info(
                    "matrix.get_event.success",
                    room_id=room_id,
                    event_id=event_id,
                    body_length=len(body),
                    body_preview=body[:100] if len(body) > 100 else body,
                )
                return body

            logger.debug(
                "matrix.get_event.no_body",
                body_value=body,
            )
            return None

        except Exception as exc:
            logger.error(
                "matrix.get_event.error",
                room_id=room_id,
                event_id=event_id,
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    async def close(self) -> None:
        """Close the client and cleanup resources."""
        await self._outbox.close()
        if self._nio_client is not None:
            await self._nio_client.close()
            self._nio_client = None
        if self._owns_http_client:
            await self._http_client.aclose()
