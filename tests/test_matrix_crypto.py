"""Tests for Matrix E2EE (End-to-End Encryption) helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from takopi_matrix.crypto import (
    CryptoManager,
    ensure_crypto_store_dir,
    get_default_crypto_store_path,
    is_e2ee_available,
)


class TestIsE2EEAvailable:
    """Test E2EE availability detection."""

    def test_e2ee_available(self) -> None:
        """E2EE is available when matrix-nio[e2e] is installed (mandatory)."""
        # Since matrix-nio[e2e] is now a mandatory dependency,
        # E2EE should always be available
        result = is_e2ee_available()
        assert result is True


class TestCryptoStorePath:
    """Test crypto store path utilities."""

    def test_get_default_crypto_store_path(self) -> None:
        """Default crypto store path is in home directory."""
        path = get_default_crypto_store_path()
        assert isinstance(path, Path)
        assert path.name == "matrix_crypto.db"
        assert ".takopi" in str(path)

    def test_ensure_crypto_store_dir_creates_parent(self, tmp_path: Path) -> None:
        """ensure_crypto_store_dir creates parent directory."""
        store_path = tmp_path / "subdir" / "crypto.db"
        assert not store_path.parent.exists()

        ensure_crypto_store_dir(store_path)

        assert store_path.parent.exists()
        assert store_path.parent.is_dir()

    def test_ensure_crypto_store_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_crypto_store_dir is idempotent."""
        store_path = tmp_path / "crypto.db"
        store_path.parent.mkdir(parents=True, exist_ok=True)

        # Should not raise even if dir exists
        ensure_crypto_store_dir(store_path)
        ensure_crypto_store_dir(store_path)

        assert store_path.parent.exists()


class TestCryptoManager:
    """Test CryptoManager class."""

    def test_init_default_path(self) -> None:
        """CryptoManager uses default path when not specified."""
        manager = CryptoManager()
        assert manager.store_path == get_default_crypto_store_path()
        assert manager._initialized is False

    def test_init_custom_path(self, tmp_path: Path) -> None:
        """CryptoManager accepts custom store path."""
        custom_path = tmp_path / "custom_crypto.db"
        manager = CryptoManager(store_path=custom_path)
        assert manager.store_path == custom_path

    def test_available_property(self) -> None:
        """available property returns True when E2EE available (libolm installed)."""
        manager = CryptoManager()
        # Since matrix-nio[e2e] is now mandatory, this should always be True
        # when tests can run (libolm must be installed)
        assert manager.available is True

    def test_ensure_store(self, tmp_path: Path) -> None:
        """ensure_store creates crypto store directory."""
        store_path = tmp_path / "subdir" / "crypto.db"
        manager = CryptoManager(store_path=store_path)

        assert not store_path.parent.exists()
        manager.ensure_store()
        assert store_path.parent.exists()

    @pytest.mark.anyio
    async def test_init_crypto_when_unavailable(self) -> None:
        """init_crypto returns False when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = await manager.init_crypto(client)

            assert result is False
            assert manager._initialized is False

    @pytest.mark.anyio
    async def test_init_crypto_not_nio_client(self) -> None:
        """init_crypto returns False for non-nio.AsyncClient."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=True):
            manager = CryptoManager()
            # Not a nio.AsyncClient
            client = "not-a-nio-client"

            result = await manager.init_crypto(client)

            assert result is False

    def test_is_room_encrypted_when_unavailable(self) -> None:
        """is_room_encrypted returns False when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = manager.is_room_encrypted(client, "!room:example.org")

            assert result is False

    def test_is_room_encrypted_not_nio_client(self) -> None:
        """is_room_encrypted returns False for non-nio.AsyncClient."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=True):
            manager = CryptoManager()
            client = "not-a-nio-client"

            result = manager.is_room_encrypted(client, "!room:example.org")

            assert result is False

    @pytest.mark.anyio
    async def test_start_verification_when_unavailable(self) -> None:
        """start_verification returns None when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = await manager.start_verification(client, "DEVICE", "@user:ex.org")

            assert result is None

    @pytest.mark.anyio
    async def test_confirm_verification_when_unavailable(self) -> None:
        """confirm_verification returns False when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = await manager.confirm_verification(client, "txn123")

            assert result is False

    @pytest.mark.anyio
    async def test_cancel_verification_when_unavailable(self) -> None:
        """cancel_verification returns False when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = await manager.cancel_verification(client, "txn123")

            assert result is False

    def test_get_verification_emojis_when_unavailable(self) -> None:
        """get_verification_emojis returns None when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = manager.get_verification_emojis(client, "txn123")

            assert result is None

    @pytest.mark.anyio
    async def test_trust_device_when_unavailable(self) -> None:
        """trust_device returns False when E2EE unavailable."""
        with patch("takopi_matrix.crypto.is_e2ee_available", return_value=False):
            manager = CryptoManager()
            client = MagicMock()

            result = await manager.trust_device(client, "@user:ex.org", "DEVICE")

            assert result is False
