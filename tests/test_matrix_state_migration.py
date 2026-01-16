"""Tests for state migration functionality."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from takopi_matrix.room_prefs import RoomPrefsStore


class TestRoomPrefsStateMigration:
    """Tests for v1 -> v2 state migration."""

    @pytest.fixture
    def prefs_path(self, tmp_path: Path) -> Path:
        """Create a temporary path for room prefs."""
        return tmp_path / "matrix_room_prefs_state.json"

    @pytest.mark.anyio
    async def test_v1_to_v2_migration(self, prefs_path: Path) -> None:
        """v1 state with rooms is migrated to v2 format."""
        # Write v1 format state
        v1_state = {
            "version": 1,
            "rooms": {
                "!room1:example.org": {"default_engine": "opus"},
                "!room2:example.org": {"default_engine": "sonnet"},
            },
        }
        prefs_path.write_text(json.dumps(v1_state))

        # Load with RoomPrefsStore (should trigger migration)
        store = RoomPrefsStore(prefs_path)

        # Verify data was migrated
        engine1 = await store.get_default_engine("!room1:example.org")
        engine2 = await store.get_default_engine("!room2:example.org")
        assert engine1 == "opus"
        assert engine2 == "sonnet"

        # Verify new fields exist with defaults
        mode1 = await store.get_trigger_mode("!room1:example.org")
        mode2 = await store.get_trigger_mode("!room2:example.org")
        assert mode1 is None  # 'all' is default, stored as None
        assert mode2 is None

        # Verify file was updated to v2
        data = json.loads(prefs_path.read_text())
        assert data["version"] == 2
        assert "trigger_mode" in data["rooms"]["!room1:example.org"]
        assert "engine_overrides" in data["rooms"]["!room1:example.org"]

    @pytest.mark.anyio
    async def test_v1_migration_preserves_all_rooms(self, prefs_path: Path) -> None:
        """Migration preserves all rooms from v1 state."""
        # Write v1 format state with many rooms
        v1_state = {
            "version": 1,
            "rooms": {
                f"!room{i}:example.org": {"default_engine": f"engine{i}"}
                for i in range(10)
            },
        }
        prefs_path.write_text(json.dumps(v1_state))

        store = RoomPrefsStore(prefs_path)

        # Verify all rooms were migrated
        for i in range(10):
            engine = await store.get_default_engine(f"!room{i}:example.org")
            assert engine == f"engine{i}"

    @pytest.mark.anyio
    async def test_v2_state_loads_directly(self, prefs_path: Path) -> None:
        """v2 state loads without migration."""
        v2_state = {
            "version": 2,
            "rooms": {
                "!room:example.org": {
                    "default_engine": "opus",
                    "trigger_mode": "mentions",
                    "engine_overrides": {"opus": {"model": "gpt-4", "reasoning": None}},
                },
            },
        }
        prefs_path.write_text(json.dumps(v2_state))

        store = RoomPrefsStore(prefs_path)

        engine = await store.get_default_engine("!room:example.org")
        mode = await store.get_trigger_mode("!room:example.org")
        assert engine == "opus"
        assert mode == "mentions"

    @pytest.mark.anyio
    async def test_future_version_resets_state(self, prefs_path: Path) -> None:
        """State with future version resets to empty (can't downgrade)."""
        future_state = {
            "version": 999,
            "rooms": {
                "!room:example.org": {"default_engine": "opus"},
            },
        }
        prefs_path.write_text(json.dumps(future_state))

        store = RoomPrefsStore(prefs_path)

        # Should be empty after reset
        engine = await store.get_default_engine("!room:example.org")
        assert engine is None

    @pytest.mark.anyio
    async def test_empty_v1_state_migrates_cleanly(self, prefs_path: Path) -> None:
        """Empty v1 state migrates to empty v2 state."""
        v1_state = {"version": 1, "rooms": {}}
        prefs_path.write_text(json.dumps(v1_state))

        store = RoomPrefsStore(prefs_path)

        # Should work without errors
        all_rooms = await store.get_all_rooms()
        assert all_rooms == {}

        # File should be v2 now
        data = json.loads(prefs_path.read_text())
        assert data["version"] == 2
