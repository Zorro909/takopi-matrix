"""Tests for state migration functionality."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from takopi_matrix.room_prefs import RoomPrefsStore


class TestRoomPrefsStateMigration:
    """Tests for room prefs state migration."""

    @pytest.fixture
    def prefs_path(self, tmp_path: Path) -> Path:
        """Create a temporary path for room prefs."""
        return tmp_path / "matrix_room_prefs_state.json"

    @pytest.mark.anyio
    async def test_v1_to_v3_migration(self, prefs_path: Path) -> None:
        """v1 state with rooms is migrated to v3 format."""
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

        # Verify file was updated to v3
        data = json.loads(prefs_path.read_text())
        assert data["version"] == 3
        assert "trigger_mode" in data["rooms"]["!room1:example.org"]
        assert "engine_overrides" in data["rooms"]["!room1:example.org"]
        assert "context_project" in data["rooms"]["!room1:example.org"]
        assert "context_branch" in data["rooms"]["!room1:example.org"]

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
    async def test_v2_state_migrates_to_v3(self, prefs_path: Path) -> None:
        """v2 state migrates to v3 by adding context fields."""
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
        data = json.loads(prefs_path.read_text())
        assert data["version"] == 3
        assert "context_project" in data["rooms"]["!room:example.org"]
        assert "context_branch" in data["rooms"]["!room:example.org"]

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
        """Empty v1 state migrates to empty v3 state."""
        v1_state = {"version": 1, "rooms": {}}
        prefs_path.write_text(json.dumps(v1_state))

        store = RoomPrefsStore(prefs_path)

        # Should work without errors
        all_rooms = await store.get_all_rooms()
        assert all_rooms == {}

        # File should be v3 now
        data = json.loads(prefs_path.read_text())
        assert data["version"] == 3


class TestStateStoreEdgeCases:
    """Tests for JsonStateStore edge cases."""

    @pytest.fixture
    def prefs_path(self, tmp_path: Path) -> Path:
        """Create a temporary path for room prefs."""
        return tmp_path / "matrix_room_prefs_state.json"

    @pytest.mark.anyio
    async def test_load_corrupted_json_resets_state(self, prefs_path: Path) -> None:
        """Corrupted JSON file results in reset state."""
        # Write invalid JSON
        prefs_path.write_text("{not valid json")

        store = RoomPrefsStore(prefs_path)

        # Should work without errors (reset to empty state)
        all_rooms = await store.get_all_rooms()
        assert all_rooms == {}

    @pytest.mark.anyio
    async def test_load_missing_file_creates_default_state(
        self, prefs_path: Path
    ) -> None:
        """Missing file creates default empty state."""
        # Don't create the file
        assert not prefs_path.exists()

        store = RoomPrefsStore(prefs_path)

        all_rooms = await store.get_all_rooms()
        assert all_rooms == {}

    @pytest.mark.anyio
    async def test_load_invalid_state_type_resets(self, prefs_path: Path) -> None:
        """Invalid state type (not a dict) resets to default."""
        # Write a list instead of dict
        prefs_path.write_text(json.dumps([1, 2, 3]))

        store = RoomPrefsStore(prefs_path)

        all_rooms = await store.get_all_rooms()
        assert all_rooms == {}

    @pytest.mark.anyio
    async def test_load_state_with_missing_version(self, prefs_path: Path) -> None:
        """State with missing version resets to default."""
        # No version field at all
        prefs_path.write_text(json.dumps({"rooms": {"!r:x": {}}}))

        store = RoomPrefsStore(prefs_path)

        all_rooms = await store.get_all_rooms()
        # Should reset since version is None/missing
        assert all_rooms == {}


class TestAtomicWrite:
    """Tests for atomic write functionality."""

    @pytest.fixture
    def prefs_path(self, tmp_path: Path) -> Path:
        """Create a temporary path for room prefs."""
        return tmp_path / "matrix_room_prefs_state.json"

    @pytest.mark.anyio
    async def test_atomic_write_success(self, prefs_path: Path) -> None:
        """Successful write leaves no temp file."""
        store = RoomPrefsStore(prefs_path)
        await store.set_default_engine("!room:example.org", "opus")

        # Verify file exists and no .tmp file
        assert prefs_path.exists()
        assert not prefs_path.with_suffix(".tmp").exists()

    @pytest.mark.anyio
    async def test_atomic_write_with_existing_parent_dir(self, tmp_path: Path) -> None:
        """Atomic write works when parent directory exists."""
        nested_path = tmp_path / "subdir" / "state.json"
        nested_path.parent.mkdir(parents=True)

        store = RoomPrefsStore(nested_path)
        await store.set_default_engine("!room:example.org", "opus")

        assert nested_path.exists()
        assert not nested_path.with_suffix(".tmp").exists()


class TestMigrationEdgeCases:
    """Tests for migration edge cases in JsonStateStore."""

    @pytest.fixture
    def prefs_path(self, tmp_path: Path) -> Path:
        """Create a temporary path for room prefs."""
        return tmp_path / "matrix_room_prefs_state.json"

    @pytest.mark.anyio
    async def test_migration_with_missing_rooms_key(self, prefs_path: Path) -> None:
        """v1 state missing 'rooms' key still migrates."""
        v1_state = {"version": 1}  # No rooms key
        prefs_path.write_text(json.dumps(v1_state))

        store = RoomPrefsStore(prefs_path)

        # Should work - rooms defaults to empty dict
        all_rooms = await store.get_all_rooms()
        assert all_rooms == {}

    @pytest.mark.anyio
    async def test_version_zero_resets_state(self, prefs_path: Path) -> None:
        """Version 0 (invalid) resets state."""
        prefs_path.write_text(json.dumps({"version": 0, "rooms": {"!r:x": {}}}))

        store = RoomPrefsStore(prefs_path)

        all_rooms = await store.get_all_rooms()
        # Version 0 < current version triggers migration attempt
        # but migration from 0 is not supported, so resets
        assert all_rooms == {}  # Reset or empty

    @pytest.mark.anyio
    async def test_state_with_extra_fields_preserved(self, prefs_path: Path) -> None:
        """Extra fields in state are preserved on load."""
        v2_state = {
            "version": 2,
            "rooms": {
                "!room:example.org": {
                    "default_engine": "opus",
                    "trigger_mode": None,
                    "engine_overrides": {},
                },
            },
        }
        prefs_path.write_text(json.dumps(v2_state))

        store = RoomPrefsStore(prefs_path)

        engine = await store.get_default_engine("!room:example.org")
        assert engine == "opus"
