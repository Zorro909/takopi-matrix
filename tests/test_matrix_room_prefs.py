"""Tests for room preferences store."""

from __future__ import annotations

from pathlib import Path

import pytest

from takopi_matrix.room_prefs import (
    RoomPrefsStore,
    resolve_prefs_path,
)


@pytest.fixture
def prefs_path(tmp_path: Path) -> Path:
    """Create a temporary path for room prefs."""
    return tmp_path / "matrix_room_prefs_state.json"


@pytest.fixture
def room_prefs(prefs_path: Path) -> RoomPrefsStore:
    """Create a room prefs store with temporary storage."""
    return RoomPrefsStore(prefs_path)


class TestResolvePrefsPath:
    def test_adjacent_to_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "takopi.toml"
        result = resolve_prefs_path(config_path)
        assert result == tmp_path / "matrix_room_prefs_state.json"

    def test_preserves_directory(self) -> None:
        config_path = Path("/home/user/.takopi/takopi.toml")
        result = resolve_prefs_path(config_path)
        assert result == Path("/home/user/.takopi/matrix_room_prefs_state.json")


class TestRoomPrefsStore:
    @pytest.mark.anyio
    async def test_get_default_engine_empty(self, room_prefs: RoomPrefsStore) -> None:
        """Empty store returns None."""
        result = await room_prefs.get_default_engine("!room:example.org")
        assert result is None

    @pytest.mark.anyio
    async def test_set_and_get_default_engine(self, room_prefs: RoomPrefsStore) -> None:
        """Can set and retrieve engine for a room."""
        room_id = "!room:example.org"
        await room_prefs.set_default_engine(room_id, "opus")
        result = await room_prefs.get_default_engine(room_id)
        assert result == "opus"

    @pytest.mark.anyio
    async def test_set_multiple_rooms(self, room_prefs: RoomPrefsStore) -> None:
        """Can set different engines for different rooms."""
        await room_prefs.set_default_engine("!room1:example.org", "opus")
        await room_prefs.set_default_engine("!room2:example.org", "sonnet")

        assert await room_prefs.get_default_engine("!room1:example.org") == "opus"
        assert await room_prefs.get_default_engine("!room2:example.org") == "sonnet"

    @pytest.mark.anyio
    async def test_clear_default_engine(self, room_prefs: RoomPrefsStore) -> None:
        """Clearing engine removes it."""
        room_id = "!room:example.org"
        await room_prefs.set_default_engine(room_id, "opus")
        await room_prefs.clear_default_engine(room_id)
        result = await room_prefs.get_default_engine(room_id)
        assert result is None

    @pytest.mark.anyio
    async def test_set_none_clears(self, room_prefs: RoomPrefsStore) -> None:
        """Setting None clears the engine."""
        room_id = "!room:example.org"
        await room_prefs.set_default_engine(room_id, "opus")
        await room_prefs.set_default_engine(room_id, None)
        result = await room_prefs.get_default_engine(room_id)
        assert result is None

    @pytest.mark.anyio
    async def test_whitespace_normalized(self, room_prefs: RoomPrefsStore) -> None:
        """Whitespace is trimmed from engine names."""
        room_id = "!room:example.org"
        await room_prefs.set_default_engine(room_id, "  opus  ")
        result = await room_prefs.get_default_engine(room_id)
        assert result == "opus"

    @pytest.mark.anyio
    async def test_empty_string_clears(self, room_prefs: RoomPrefsStore) -> None:
        """Empty string clears the engine."""
        room_id = "!room:example.org"
        await room_prefs.set_default_engine(room_id, "opus")
        await room_prefs.set_default_engine(room_id, "   ")
        result = await room_prefs.get_default_engine(room_id)
        assert result is None

    @pytest.mark.anyio
    async def test_get_all_rooms(self, room_prefs: RoomPrefsStore) -> None:
        """Can get all rooms with their engines."""
        await room_prefs.set_default_engine("!room1:example.org", "opus")
        await room_prefs.set_default_engine("!room2:example.org", "sonnet")

        all_rooms = await room_prefs.get_all_rooms()
        assert all_rooms == {
            "!room1:example.org": "opus",
            "!room2:example.org": "sonnet",
        }

    @pytest.mark.anyio
    async def test_persistence(self, prefs_path: Path) -> None:
        """State persists across store instances."""
        room_id = "!room:example.org"

        # Set with first instance
        store1 = RoomPrefsStore(prefs_path)
        await store1.set_default_engine(room_id, "opus")

        # Read with new instance
        store2 = RoomPrefsStore(prefs_path)
        result = await store2.get_default_engine(room_id)
        assert result == "opus"

    @pytest.mark.anyio
    async def test_hot_reload(self, prefs_path: Path) -> None:
        """Store reloads when file is modified externally."""
        import json
        import os
        import time

        room_id = "!room:example.org"
        store = RoomPrefsStore(prefs_path)

        # Set initial value
        await store.set_default_engine(room_id, "opus")

        # Modify file externally
        data = {
            "version": 1,
            "rooms": {room_id: {"default_engine": "sonnet"}},
        }
        prefs_path.write_text(json.dumps(data))

        # Ensure mtime changes (some filesystems have low resolution)
        future_ns = time.time_ns() + 1_000_000  # 1ms in future
        os.utime(prefs_path, ns=(future_ns, future_ns))

        # Should reload and see new value
        result = await store.get_default_engine(room_id)
        assert result == "sonnet"
