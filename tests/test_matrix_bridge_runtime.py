"""Tests for bridge/runtime.py - main runtime loop and startup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from takopi.api import RenderedMessage, MessageRef
from takopi_matrix.bridge.runtime import (
    _persist_new_rooms,
    _send_startup,
    _initialize_e2ee_if_available,
    _trust_room_devices_if_e2ee,
    _startup_sequence,
)


# --- Test fixtures ---


class FakeTransport:
    """Fake transport for testing."""

    def __init__(self):
        self.send_calls: list[dict] = []
        self._next_id = 1

    async def send(
        self, *, channel_id, message, options=None
    ) -> MessageRef | None:
        ref = MessageRef(channel_id=channel_id, message_id=f"$sent{self._next_id}")
        self._next_id += 1
        self.send_calls.append({
            "channel_id": channel_id,
            "message": message,
            "options": options,
        })
        return ref


class FakePresenter:
    """Fake presenter for testing."""

    def render_progress(self, state, elapsed_s, label=None):
        return RenderedMessage(text=f"progress: {label}")

    def render_final(self, state, elapsed_s, status, answer):
        return RenderedMessage(text=f"final: {status} - {answer}")


class FakeExecCfg:
    """Fake ExecBridgeConfig for testing."""

    def __init__(self, transport: FakeTransport):
        self.transport = transport
        self.presenter = FakePresenter()
        self.final_notify = True


class FakeClient:
    """Fake Matrix client for testing."""

    def __init__(self):
        self.user_id = "@bot:example.org"
        self.e2ee_available = False
        self._login_result = True
        self._init_e2ee_result = True
        self.sync_calls = 0
        self.trust_calls: list[str] = []
        self.ensure_keys_calls: list[str] = []
        self.typing_calls: list[tuple[str, bool]] = []
        self._display_name = "Bot"

    async def login(self) -> bool:
        return self._login_result

    async def sync(self, timeout_ms: int = 30000):
        self.sync_calls += 1
        return MagicMock()

    async def init_e2ee(self) -> bool:
        return self._init_e2ee_result

    async def trust_room_devices(self, room_id: str) -> None:
        self.trust_calls.append(room_id)

    async def ensure_room_keys(self, room_id: str) -> None:
        self.ensure_keys_calls.append(room_id)

    async def send_typing(self, room_id: str, typing: bool) -> None:
        self.typing_calls.append((room_id, typing))

    async def get_display_name(self) -> str | None:
        return self._display_name

    async def close(self) -> None:
        pass


class FakeRuntimeConfig:
    """Fake runtime config."""

    pass


class FakeMatrixBridgeConfig:
    """Fake MatrixBridgeConfig for testing."""

    def __init__(self, client=None, transport=None):
        self.client = client or FakeClient()
        transport = transport or FakeTransport()
        self.exec_cfg = FakeExecCfg(transport)
        self.room_ids = ["!room1:example.org", "!room2:example.org"]
        self.send_startup_message = True
        self.startup_msg = "takopi is online!"
        self.config_path = None
        self.runtime = FakeRuntimeConfig()
        self.room_project_map = {}


# --- _persist_new_rooms tests ---


@pytest.mark.anyio
async def test_persist_new_rooms_none_path() -> None:
    """persist_new_rooms returns early if config_path is None."""
    # Should not raise
    await _persist_new_rooms(["!room:x"], None)


@pytest.mark.anyio
async def test_persist_new_rooms_empty_rooms() -> None:
    """persist_new_rooms returns early if no new rooms."""
    await _persist_new_rooms([], Path("/fake/config.toml"))


@pytest.mark.anyio
async def test_persist_new_rooms_not_path_object() -> None:
    """persist_new_rooms returns early if config_path is not a Path."""
    await _persist_new_rooms(["!room:x"], "/string/path")  # type: ignore


@pytest.mark.anyio
async def test_persist_new_rooms_tomlkit_not_available(tmp_path: Path) -> None:
    """persist_new_rooms handles missing tomlkit gracefully."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[transports.matrix]\nroom_ids = ["!existing:x"]\n')

    with patch.dict("sys.modules", {"tomlkit": None}):
        # This should not raise even if tomlkit import fails
        await _persist_new_rooms(["!new:x"], config_path)


@pytest.mark.anyio
async def test_persist_new_rooms_success(tmp_path: Path) -> None:
    """persist_new_rooms adds new rooms to config."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[transports.matrix]\nroom_ids = ["!existing:example.org"]\n'
    )

    await _persist_new_rooms(["!new:example.org"], config_path)

    # Re-read and verify
    import tomlkit

    config = tomlkit.parse(config_path.read_text())
    room_ids = config["transports"]["matrix"]["room_ids"]  # type: ignore
    assert "!existing:example.org" in room_ids
    assert "!new:example.org" in room_ids


@pytest.mark.anyio
async def test_persist_new_rooms_duplicate_ignored(tmp_path: Path) -> None:
    """persist_new_rooms ignores duplicate room IDs."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[transports.matrix]\nroom_ids = ["!existing:example.org"]\n'
    )

    # Try to add existing room
    await _persist_new_rooms(["!existing:example.org"], config_path)

    # File should not have changed (or has same content)
    import tomlkit

    config = tomlkit.parse(config_path.read_text())
    room_ids = list(config["transports"]["matrix"]["room_ids"])  # type: ignore
    assert room_ids.count("!existing:example.org") == 1


@pytest.mark.anyio
async def test_persist_new_rooms_creates_sections(tmp_path: Path) -> None:
    """persist_new_rooms creates missing sections in config."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("# Empty config\n")

    await _persist_new_rooms(["!new:example.org"], config_path)

    import tomlkit

    config = tomlkit.parse(config_path.read_text())
    assert "transports" in config
    assert "matrix" in config["transports"]  # type: ignore
    assert "!new:example.org" in config["transports"]["matrix"]["room_ids"]  # type: ignore


@pytest.mark.anyio
async def test_persist_new_rooms_exception_handling(tmp_path: Path) -> None:
    """persist_new_rooms handles exceptions gracefully."""
    # Provide a path to a non-existent directory
    config_path = tmp_path / "nonexistent" / "config.toml"

    # Should not raise
    await _persist_new_rooms(["!new:x"], config_path)


# --- _send_startup tests ---


@pytest.mark.anyio
async def test_send_startup_disabled() -> None:
    """_send_startup does nothing when disabled."""
    cfg = FakeMatrixBridgeConfig()
    cfg.send_startup_message = False

    await _send_startup(cfg)  # type: ignore

    assert len(cfg.exec_cfg.transport.send_calls) == 0


@pytest.mark.anyio
async def test_send_startup_sends_to_all_rooms() -> None:
    """_send_startup sends message to all configured rooms."""
    cfg = FakeMatrixBridgeConfig()

    await _send_startup(cfg)  # type: ignore

    assert len(cfg.exec_cfg.transport.send_calls) == 2
    channels = [c["channel_id"] for c in cfg.exec_cfg.transport.send_calls]
    assert "!room1:example.org" in channels
    assert "!room2:example.org" in channels


@pytest.mark.anyio
async def test_send_startup_uses_configured_message() -> None:
    """_send_startup uses the configured startup message."""
    cfg = FakeMatrixBridgeConfig()
    cfg.startup_msg = "Custom startup message"

    await _send_startup(cfg)  # type: ignore

    call = cfg.exec_cfg.transport.send_calls[0]
    assert "Custom startup message" in call["message"].text


# --- _initialize_e2ee_if_available tests ---


@pytest.mark.anyio
async def test_initialize_e2ee_not_available() -> None:
    """_initialize_e2ee_if_available does nothing when E2EE not available."""
    client = FakeClient()
    client.e2ee_available = False
    cfg = FakeMatrixBridgeConfig(client=client)

    await _initialize_e2ee_if_available(cfg)  # type: ignore

    # No e2ee methods should be called


@pytest.mark.anyio
async def test_initialize_e2ee_success() -> None:
    """_initialize_e2ee_if_available initializes E2EE when available."""
    client = FakeClient()
    client.e2ee_available = True
    client._init_e2ee_result = True
    cfg = FakeMatrixBridgeConfig(client=client)

    await _initialize_e2ee_if_available(cfg)  # type: ignore

    # Should succeed without raising


@pytest.mark.anyio
async def test_initialize_e2ee_failure() -> None:
    """_initialize_e2ee_if_available handles init failure."""
    client = FakeClient()
    client.e2ee_available = True
    client._init_e2ee_result = False
    cfg = FakeMatrixBridgeConfig(client=client)

    await _initialize_e2ee_if_available(cfg)  # type: ignore

    # Should not raise even on failure


# --- _trust_room_devices_if_e2ee tests ---


@pytest.mark.anyio
async def test_trust_room_devices_not_available() -> None:
    """_trust_room_devices_if_e2ee does nothing when E2EE not available."""
    client = FakeClient()
    client.e2ee_available = False
    cfg = FakeMatrixBridgeConfig(client=client)

    await _trust_room_devices_if_e2ee(cfg)  # type: ignore

    assert len(client.trust_calls) == 0
    assert len(client.ensure_keys_calls) == 0


@pytest.mark.anyio
async def test_trust_room_devices_all_rooms() -> None:
    """_trust_room_devices_if_e2ee trusts devices in all rooms."""
    client = FakeClient()
    client.e2ee_available = True
    cfg = FakeMatrixBridgeConfig(client=client)

    await _trust_room_devices_if_e2ee(cfg)  # type: ignore

    assert len(client.trust_calls) == 2
    assert "!room1:example.org" in client.trust_calls
    assert "!room2:example.org" in client.trust_calls

    assert len(client.ensure_keys_calls) == 2


# --- _startup_sequence tests ---


@pytest.mark.anyio
async def test_startup_sequence_login_fails() -> None:
    """_startup_sequence returns False when login fails."""
    client = FakeClient()
    client._login_result = False
    cfg = FakeMatrixBridgeConfig(client=client)

    result = await _startup_sequence(cfg)  # type: ignore

    assert result is False


@pytest.mark.anyio
async def test_startup_sequence_success() -> None:
    """_startup_sequence returns True when all steps succeed."""
    client = FakeClient()
    client._login_result = True
    cfg = FakeMatrixBridgeConfig(client=client)

    result = await _startup_sequence(cfg)  # type: ignore

    assert result is True
    assert client.sync_calls == 1  # Initial sync

    # Typing indicators should be sent
    assert len(client.typing_calls) == 4  # True + False for each of 2 rooms


@pytest.mark.anyio
async def test_startup_sequence_with_e2ee() -> None:
    """_startup_sequence initializes E2EE when available."""
    client = FakeClient()
    client.e2ee_available = True
    cfg = FakeMatrixBridgeConfig(client=client)

    result = await _startup_sequence(cfg)  # type: ignore

    assert result is True
    # Should have trusted devices in all rooms
    assert len(client.trust_calls) == 2
    assert len(client.ensure_keys_calls) == 2
