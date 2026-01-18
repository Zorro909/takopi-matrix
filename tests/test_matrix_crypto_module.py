"""Tests for crypto.py - E2EE helpers."""

from __future__ import annotations

from pathlib import Path
import pytest

from takopi_matrix.crypto import (
    is_e2ee_available,
    get_default_crypto_store_path,
    ensure_crypto_store_dir,
    CryptoManager,
)


# --- is_e2ee_available tests ---


def test_is_e2ee_available_returns_bool() -> None:
    """is_e2ee_available returns a boolean."""
    result = is_e2ee_available()
    assert isinstance(result, bool)


def test_is_e2ee_available_consistent() -> None:
    """is_e2ee_available returns consistent results."""
    result1 = is_e2ee_available()
    result2 = is_e2ee_available()
    assert result1 == result2


# --- get_default_crypto_store_path tests ---


def test_get_default_crypto_store_path_returns_path() -> None:
    """Returns a Path object."""
    result = get_default_crypto_store_path()
    assert isinstance(result, Path)


def test_get_default_crypto_store_path_in_home() -> None:
    """Default path is in home directory."""
    result = get_default_crypto_store_path()
    assert ".takopi" in str(result)
    assert "matrix_crypto" in str(result)


# --- ensure_crypto_store_dir tests ---


def test_ensure_crypto_store_dir_creates_parent(tmp_path: Path) -> None:
    """Creates parent directories if they don't exist."""
    store_path = tmp_path / "nested" / "dir" / "crypto.db"
    ensure_crypto_store_dir(store_path)
    assert store_path.parent.exists()


def test_ensure_crypto_store_dir_existing(tmp_path: Path) -> None:
    """Doesn't fail if directory already exists."""
    store_path = tmp_path / "crypto.db"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    # Should not raise
    ensure_crypto_store_dir(store_path)
    assert store_path.parent.exists()


# --- CryptoManager tests ---


def test_crypto_manager_init_default_path() -> None:
    """CryptoManager uses default path if none provided."""
    manager = CryptoManager()
    assert manager.store_path == get_default_crypto_store_path()


def test_crypto_manager_init_custom_path(tmp_path: Path) -> None:
    """CryptoManager uses custom path if provided."""
    custom_path = tmp_path / "custom_crypto.db"
    manager = CryptoManager(store_path=custom_path)
    assert manager.store_path == custom_path


def test_crypto_manager_available_property() -> None:
    """available property returns boolean."""
    manager = CryptoManager()
    assert isinstance(manager.available, bool)


def test_crypto_manager_ensure_store(tmp_path: Path) -> None:
    """ensure_store creates the directory."""
    store_path = tmp_path / "new_dir" / "crypto.db"
    manager = CryptoManager(store_path=store_path)
    manager.ensure_store()
    assert store_path.parent.exists()


@pytest.mark.anyio
async def test_crypto_manager_init_crypto_no_e2ee() -> None:
    """init_crypto returns False if E2EE not available or wrong client type."""
    manager = CryptoManager()

    # Pass a non-nio client
    class FakeClient:
        pass

    result = await manager.init_crypto(FakeClient())
    assert result is False


def test_crypto_manager_is_room_encrypted_wrong_client() -> None:
    """is_room_encrypted returns False for wrong client type."""
    manager = CryptoManager()

    class FakeClient:
        pass

    result = manager.is_room_encrypted(FakeClient(), "!room:example.org")
    assert result is False


def test_crypto_manager_is_room_encrypted_no_e2ee() -> None:
    """is_room_encrypted returns False when not available."""
    manager = CryptoManager()
    manager._e2ee_available = False

    class FakeClient:
        pass

    result = manager.is_room_encrypted(FakeClient(), "!room:example.org")
    assert result is False


@pytest.mark.anyio
async def test_crypto_manager_start_verification_wrong_client() -> None:
    """start_verification returns None for wrong client type."""
    manager = CryptoManager()

    class FakeClient:
        pass

    result = await manager.start_verification(
        FakeClient(), "device_id", "@user:example.org"
    )
    assert result is None


@pytest.mark.anyio
async def test_crypto_manager_start_verification_no_e2ee() -> None:
    """start_verification returns None when E2EE not available."""
    manager = CryptoManager()
    manager._e2ee_available = False

    class FakeClient:
        pass

    result = await manager.start_verification(
        FakeClient(), "device_id", "@user:example.org"
    )
    assert result is None


@pytest.mark.anyio
async def test_crypto_manager_confirm_verification_wrong_client() -> None:
    """confirm_verification returns False for wrong client type."""
    manager = CryptoManager()

    class FakeClient:
        pass

    result = await manager.confirm_verification(FakeClient(), "tx_id")
    assert result is False


@pytest.mark.anyio
async def test_crypto_manager_cancel_verification_wrong_client() -> None:
    """cancel_verification returns False for wrong client type."""
    manager = CryptoManager()

    class FakeClient:
        pass

    result = await manager.cancel_verification(FakeClient(), "tx_id")
    assert result is False


@pytest.mark.anyio
async def test_crypto_manager_trust_device_wrong_client() -> None:
    """trust_device returns False for wrong client type."""
    manager = CryptoManager()

    class FakeClient:
        pass

    result = await manager.trust_device(
        FakeClient(), "@user:example.org", "device_id"
    )
    assert result is False


@pytest.mark.anyio
async def test_crypto_manager_trust_device_no_e2ee() -> None:
    """trust_device returns False when E2EE not available."""
    manager = CryptoManager()
    manager._e2ee_available = False

    class FakeClient:
        pass

    result = await manager.trust_device(
        FakeClient(), "@user:example.org", "device_id"
    )
    assert result is False
