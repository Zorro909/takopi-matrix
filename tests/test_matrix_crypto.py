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

    @pytest.mark.anyio
    async def test_start_verification_not_nio_client(self) -> None:
        """start_verification returns None for non-nio.AsyncClient."""
        manager = CryptoManager()
        client = "not-a-nio-client"

        result = await manager.start_verification(client, "DEVICE", "@user:ex.org")

        assert result is None

    @pytest.mark.anyio
    async def test_confirm_verification_not_nio_client(self) -> None:
        """confirm_verification returns False for non-nio.AsyncClient."""
        manager = CryptoManager()
        client = "not-a-nio-client"

        result = await manager.confirm_verification(client, "txn123")

        assert result is False

    @pytest.mark.anyio
    async def test_cancel_verification_not_nio_client(self) -> None:
        """cancel_verification returns False for non-nio.AsyncClient."""
        manager = CryptoManager()
        client = "not-a-nio-client"

        result = await manager.cancel_verification(client, "txn123")

        assert result is False

    def test_get_verification_emojis_not_nio_client(self) -> None:
        """get_verification_emojis returns None for non-nio.AsyncClient."""
        manager = CryptoManager()
        client = "not-a-nio-client"

        result = manager.get_verification_emojis(client, "txn123")

        assert result is None

    @pytest.mark.anyio
    async def test_trust_device_not_nio_client(self) -> None:
        """trust_device returns False for non-nio.AsyncClient."""
        manager = CryptoManager()
        client = "not-a-nio-client"

        result = await manager.trust_device(client, "@user:ex.org", "DEVICE")

        assert result is False

    def test_is_room_encrypted_room_not_found(self) -> None:
        """is_room_encrypted returns False when room not found."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.rooms = {}

        result = manager.is_room_encrypted(client, "!room:example.org")

        assert result is False

    def test_is_room_encrypted_room_found_not_encrypted(self) -> None:
        """is_room_encrypted returns False when room is not encrypted."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        room = MagicMock()
        room.encrypted = False
        client.rooms = {"!room:example.org": room}

        result = manager.is_room_encrypted(client, "!room:example.org")

        assert result is False

    def test_is_room_encrypted_room_found_encrypted(self) -> None:
        """is_room_encrypted returns True when room is encrypted."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        room = MagicMock()
        room.encrypted = True
        client.rooms = {"!room:example.org": room}

        result = manager.is_room_encrypted(client, "!room:example.org")

        assert result is True

    def test_is_room_encrypted_exception(self) -> None:
        """is_room_encrypted returns False on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.rooms = MagicMock()
        client.rooms.get.side_effect = RuntimeError("boom")

        result = manager.is_room_encrypted(client, "!room:example.org")

        assert result is False

    @pytest.mark.anyio
    async def test_init_crypto_olm_not_initialized(self) -> None:
        """init_crypto returns False when olm is None."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.olm = None

        result = await manager.init_crypto(client)

        assert result is False
        assert manager._initialized is False

    @pytest.mark.anyio
    async def test_init_crypto_success(self) -> None:
        """init_crypto returns True when olm is initialized."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.olm = MagicMock()  # Not None
        client.device_id = "DEVICE123"
        client.user_id = "@user:example.org"

        result = await manager.init_crypto(client)

        assert result is True
        assert manager._initialized is True

    @pytest.mark.anyio
    async def test_init_crypto_exception(self) -> None:
        """init_crypto returns False on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        # Make accessing olm raise an exception
        type(client).olm = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        result = await manager.init_crypto(client)

        assert result is False

    @pytest.mark.anyio
    async def test_start_verification_success(self) -> None:
        """start_verification returns transaction_id on success."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        response = MagicMock()
        response.transaction_id = "txn-456"
        client.start_key_verification.return_value = response

        result = await manager.start_verification(client, "DEVICE", "@user:ex.org")

        assert result == "txn-456"
        client.start_key_verification.assert_called_once_with("DEVICE", "@user:ex.org")

    @pytest.mark.anyio
    async def test_start_verification_error_response(self) -> None:
        """start_verification returns None on ToDeviceError."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        error_response = MagicMock(spec=nio.ToDeviceError)
        error_response.message = "Failed"
        client.start_key_verification.return_value = error_response

        result = await manager.start_verification(client, "DEVICE", "@user:ex.org")

        assert result is None

    @pytest.mark.anyio
    async def test_start_verification_exception(self) -> None:
        """start_verification returns None on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.start_key_verification.side_effect = RuntimeError("boom")

        result = await manager.start_verification(client, "DEVICE", "@user:ex.org")

        assert result is None

    @pytest.mark.anyio
    async def test_confirm_verification_success(self) -> None:
        """confirm_verification returns True on success."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        response = MagicMock()  # Not ToDeviceError
        client.confirm_short_auth_string.return_value = response

        result = await manager.confirm_verification(client, "txn123")

        assert result is True
        client.confirm_short_auth_string.assert_called_once_with("txn123")

    @pytest.mark.anyio
    async def test_confirm_verification_error_response(self) -> None:
        """confirm_verification returns False on ToDeviceError."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        error_response = MagicMock(spec=nio.ToDeviceError)
        error_response.message = "Failed"
        client.confirm_short_auth_string.return_value = error_response

        result = await manager.confirm_verification(client, "txn123")

        assert result is False

    @pytest.mark.anyio
    async def test_confirm_verification_exception(self) -> None:
        """confirm_verification returns False on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.confirm_short_auth_string.side_effect = RuntimeError("boom")

        result = await manager.confirm_verification(client, "txn123")

        assert result is False

    @pytest.mark.anyio
    async def test_cancel_verification_success(self) -> None:
        """cancel_verification returns True on success."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        response = MagicMock()  # Not ToDeviceError
        client.cancel_key_verification.return_value = response

        result = await manager.cancel_verification(client, "txn123")

        assert result is True
        client.cancel_key_verification.assert_called_once_with("txn123")

    @pytest.mark.anyio
    async def test_cancel_verification_error_response(self) -> None:
        """cancel_verification returns False on ToDeviceError."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        error_response = MagicMock(spec=nio.ToDeviceError)
        error_response.message = "Failed"
        client.cancel_key_verification.return_value = error_response

        result = await manager.cancel_verification(client, "txn123")

        assert result is False

    @pytest.mark.anyio
    async def test_cancel_verification_exception(self) -> None:
        """cancel_verification returns False on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.cancel_key_verification.side_effect = RuntimeError("boom")

        result = await manager.cancel_verification(client, "txn123")

        assert result is False

    def test_get_verification_emojis_no_sas(self) -> None:
        """get_verification_emojis returns None when no active SAS."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.get_active_sas.return_value = None

        result = manager.get_verification_emojis(client, "txn123")

        assert result is None

    def test_get_verification_emojis_no_emoji(self) -> None:
        """get_verification_emojis returns None when no emojis."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        sas = MagicMock()
        sas.get_emoji.return_value = []
        client.get_active_sas.return_value = sas

        result = manager.get_verification_emojis(client, "txn123")

        assert result is None

    def test_get_verification_emojis_success(self) -> None:
        """get_verification_emojis returns emoji list on success."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        sas = MagicMock()
        emoji1 = MagicMock()
        emoji1.emoji = "🐱"
        emoji1.description = "Cat"
        emoji2 = MagicMock()
        emoji2.emoji = "🐶"
        emoji2.description = "Dog"
        sas.get_emoji.return_value = [emoji1, emoji2]
        client.get_active_sas.return_value = sas

        result = manager.get_verification_emojis(client, "txn123")

        assert result == [("🐱", "Cat"), ("🐶", "Dog")]

    def test_get_verification_emojis_exception(self) -> None:
        """get_verification_emojis returns None on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.get_active_sas.side_effect = RuntimeError("boom")

        result = manager.get_verification_emojis(client, "txn123")

        assert result is None

    @pytest.mark.anyio
    async def test_trust_device_success(self) -> None:
        """trust_device returns True on success."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.verify_device = MagicMock()

        # Patch OlmDevice since it might be in nio.crypto
        with patch("nio.OlmDevice", create=True) as mock_olm_device:
            mock_olm_device.return_value = MagicMock()
            result = await manager.trust_device(client, "@user:ex.org", "DEVICE")

        assert result is True
        client.verify_device.assert_called_once()

    @pytest.mark.anyio
    async def test_trust_device_exception(self) -> None:
        """trust_device returns False on exception."""
        import nio

        manager = CryptoManager()
        client = MagicMock(spec=nio.AsyncClient)
        client.verify_device.side_effect = RuntimeError("boom")

        result = await manager.trust_device(client, "@user:ex.org", "DEVICE")

        assert result is False
