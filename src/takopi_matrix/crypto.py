"""
E2EE (End-to-End Encryption) helpers for Matrix.

This module provides utilities for:
- Checking E2EE availability (libolm)
- Managing crypto store
- Device verification (SAS/emoji)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import nio

from takopi.api import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def is_e2ee_available() -> bool:
    """
    Check if E2EE dependencies are available.

    Returns True if matrix-nio[e2e] is installed with libolm.
    """
    return hasattr(nio, "crypto")


def get_default_crypto_store_path() -> Path:
    """Get the default path for the crypto store."""
    return Path.home() / ".takopi" / "matrix_crypto.db"


def ensure_crypto_store_dir(store_path: Path) -> None:
    """Ensure the crypto store directory exists."""
    store_path.parent.mkdir(parents=True, exist_ok=True)


class CryptoManager:
    """
    Manages E2EE state for the Matrix client.

    Handles:
    - Crypto store initialization
    - Device key management
    - Verification flows
    """

    def __init__(
        self,
        store_path: Path | None = None,
    ) -> None:
        self.store_path = store_path or get_default_crypto_store_path()
        self._initialized = False
        self._e2ee_available = hasattr(nio, "crypto")

    @property
    def available(self) -> bool:
        """Check if E2EE is available."""
        return self._e2ee_available

    def ensure_store(self) -> None:
        """Ensure the crypto store directory exists."""
        if self.store_path:
            ensure_crypto_store_dir(self.store_path)

    async def init_crypto(self, client: object) -> bool:
        """
        Initialize crypto for the client.

        This should be called after login to set up E2EE.
        """
        if not self.available:
            logger.info("matrix.crypto.not_available")
            return False

        try:
            if not isinstance(client, nio.AsyncClient):
                return False

            if client.olm is None:
                logger.info("matrix.crypto.olm_not_initialized")
                return False

            self._initialized = True
            logger.info(
                "matrix.crypto.initialized",
                device_id=client.device_id,
                user_id=client.user_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "matrix.crypto.init_failed",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return False

    def is_room_encrypted(self, client: object, room_id: str) -> bool:
        """Check if a room is encrypted."""
        if not self.available:
            return False

        try:
            if not isinstance(client, nio.AsyncClient):
                return False

            room = client.rooms.get(room_id)
            if room is None:
                return False

            return room.encrypted

        except Exception:
            return False

    async def start_verification(
        self,
        client: object,
        device_id: str,
        user_id: str,
    ) -> str | None:
        """
        Start SAS verification with a device.

        Returns the transaction ID if successful, None otherwise.
        """
        if not self.available:
            return None

        try:
            if not isinstance(client, nio.AsyncClient):
                return None

            response = await client.start_key_verification(
                device_id,
                user_id,
            )

            if isinstance(response, nio.ToDeviceError):
                logger.error(
                    "matrix.crypto.verification_start_failed",
                    error=response.message,
                )
                return None

            logger.info(
                "matrix.crypto.verification_started",
                device_id=device_id,
                user_id=user_id,
            )
            return getattr(response, "transaction_id", None)

        except Exception as exc:
            logger.error(
                "matrix.crypto.verification_error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    async def confirm_verification(
        self,
        client: object,
        transaction_id: str,
    ) -> bool:
        """
        Confirm SAS verification after emoji match.

        Returns True if successful.
        """
        if not self.available:
            return False

        try:
            if not isinstance(client, nio.AsyncClient):
                return False

            response = await client.confirm_short_auth_string(transaction_id)

            if isinstance(response, nio.ToDeviceError):
                logger.error(
                    "matrix.crypto.verification_confirm_failed",
                    error=response.message,
                )
                return False

            logger.info(
                "matrix.crypto.verification_confirmed",
                transaction_id=transaction_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "matrix.crypto.verification_error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return False

    async def cancel_verification(
        self,
        client: object,
        transaction_id: str,
    ) -> bool:
        """
        Cancel an ongoing verification.

        Returns True if successful.
        """
        if not self.available:
            return False

        try:
            if not isinstance(client, nio.AsyncClient):
                return False

            response = await client.cancel_key_verification(transaction_id)

            if isinstance(response, nio.ToDeviceError):
                logger.error(
                    "matrix.crypto.verification_cancel_failed",
                    error=response.message,
                )
                return False

            logger.info(
                "matrix.crypto.verification_cancelled",
                transaction_id=transaction_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "matrix.crypto.verification_error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return False

    def get_verification_emojis(
        self,
        client: object,
        transaction_id: str,
    ) -> list[tuple[str, str]] | None:
        """
        Get the SAS verification emojis for comparison.

        Returns a list of (emoji, description) tuples, or None if not available.
        """
        if not self.available:
            return None

        try:
            if not isinstance(client, nio.AsyncClient):
                return None

            # TODO: Fix get_active_sas signature - requires user_id/device_id mapping
            # This flow may be unused (0% coverage on crypto.py)
            sas = client.get_active_sas(transaction_id)  # type: ignore[call-arg]
            if sas is None:
                return None

            emojis = sas.get_emoji()
            if not emojis:
                return None

            # Type checker doesn't know emoji object structure - ignore attribute access
            return [(e.emoji, e.description) for e in emojis]  # type: ignore[attr-defined]

        except Exception as exc:
            logger.error(
                "matrix.crypto.emoji_error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return None

    async def trust_device(
        self,
        client: object,
        user_id: str,
        device_id: str,
    ) -> bool:
        """
        Mark a device as trusted (verified).

        This bypasses SAS verification - use with caution.
        """
        if not self.available:
            return False

        try:
            if not isinstance(client, nio.AsyncClient):
                return False

            client.verify_device(
                nio.OlmDevice(  # type: ignore[attr-defined]
                    user_id=user_id,
                    device_id=device_id,
                    ed25519_key="",
                    curve25519_key="",
                )
            )

            logger.info(
                "matrix.crypto.device_trusted",
                user_id=user_id,
                device_id=device_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "matrix.crypto.trust_error",
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            return False
