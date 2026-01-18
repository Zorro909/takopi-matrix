"""Extended tests for room_prefs.py - Per-room engine preferences."""

from __future__ import annotations

from pathlib import Path
import pytest

from takopi_matrix.room_prefs import (
    RoomPrefsStore,
    resolve_prefs_path,
    _room_key,
    _normalize_text,
    _normalize_trigger_mode,
    _normalize_engine_id,
    _new_state,
    STATE_FILENAME,
)


# --- resolve_prefs_path tests ---


def test_resolve_prefs_path_basic(tmp_path: Path) -> None:
    """Resolves prefs path next to config."""
    config_path = tmp_path / "takopi.toml"
    result = resolve_prefs_path(config_path)
    assert result.name == STATE_FILENAME
    assert result.parent == tmp_path


def test_resolve_prefs_path_nested(tmp_path: Path) -> None:
    """Resolves prefs path in nested directory."""
    config_path = tmp_path / "config" / "app.toml"
    result = resolve_prefs_path(config_path)
    assert result.name == STATE_FILENAME
    assert result.parent == tmp_path / "config"


# --- _room_key tests ---


def test_room_key_preserves_id() -> None:
    """Room ID is preserved as key."""
    assert _room_key("!room:example.org") == "!room:example.org"


def test_room_key_complex_id() -> None:
    """Complex room IDs are preserved."""
    room_id = "!abc123XYZ:matrix.org"
    assert _room_key(room_id) == room_id


# --- _normalize_text tests ---


def test_normalize_text_strips_whitespace() -> None:
    """Whitespace is stripped."""
    assert _normalize_text("  hello  ") == "hello"


def test_normalize_text_empty_returns_none() -> None:
    """Empty string returns None."""
    assert _normalize_text("") is None
    assert _normalize_text("   ") is None


def test_normalize_text_none_returns_none() -> None:
    """None input returns None."""
    assert _normalize_text(None) is None


def test_normalize_text_preserves_content() -> None:
    """Content is preserved."""
    assert _normalize_text("hello world") == "hello world"


# --- _normalize_trigger_mode tests ---


def test_normalize_trigger_mode_mentions() -> None:
    """'mentions' is preserved."""
    assert _normalize_trigger_mode("mentions") == "mentions"
    assert _normalize_trigger_mode("MENTIONS") == "mentions"
    assert _normalize_trigger_mode("  mentions  ") == "mentions"


def test_normalize_trigger_mode_all() -> None:
    """'all' returns None (default)."""
    assert _normalize_trigger_mode("all") is None
    assert _normalize_trigger_mode("ALL") is None


def test_normalize_trigger_mode_invalid() -> None:
    """Invalid values return None."""
    assert _normalize_trigger_mode("invalid") is None
    assert _normalize_trigger_mode("partial") is None


def test_normalize_trigger_mode_none() -> None:
    """None returns None."""
    assert _normalize_trigger_mode(None) is None


def test_normalize_trigger_mode_empty() -> None:
    """Empty string returns None."""
    assert _normalize_trigger_mode("") is None


# --- _normalize_engine_id tests ---


def test_normalize_engine_id_lowercase() -> None:
    """Engine ID is lowercased."""
    assert _normalize_engine_id("Claude") == "claude"
    assert _normalize_engine_id("CODEX") == "codex"


def test_normalize_engine_id_strips() -> None:
    """Whitespace is stripped."""
    assert _normalize_engine_id("  claude  ") == "claude"


def test_normalize_engine_id_none() -> None:
    """None returns None."""
    assert _normalize_engine_id(None) is None


def test_normalize_engine_id_empty() -> None:
    """Empty string returns None."""
    assert _normalize_engine_id("") is None


# --- _new_state tests ---


def test_new_state_version() -> None:
    """New state has current version."""
    state = _new_state()
    assert state.version == 2


def test_new_state_empty_rooms() -> None:
    """New state has empty rooms."""
    state = _new_state()
    assert state.rooms == {}


# --- RoomPrefsStore tests ---


def test_room_prefs_store_init(tmp_path: Path) -> None:
    """Store initializes with empty state."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    assert store._state is not None


@pytest.mark.anyio
async def test_room_prefs_store_get_default_engine_none(tmp_path: Path) -> None:
    """get_default_engine returns None for unknown room."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    result = await store.get_default_engine("!unknown:example.org")
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_set_and_get_default_engine(tmp_path: Path) -> None:
    """set_default_engine stores and get_default_engine retrieves."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_default_engine(room_id, "claude")
    result = await store.get_default_engine(room_id)

    assert result == "claude"


@pytest.mark.anyio
async def test_room_prefs_store_set_default_engine_normalizes(tmp_path: Path) -> None:
    """set_default_engine normalizes the engine ID."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_default_engine(room_id, "  CLAUDE  ")
    result = await store.get_default_engine(room_id)

    # Note: _normalize_text is used, which strips but doesn't lowercase
    assert result == "CLAUDE"


@pytest.mark.anyio
async def test_room_prefs_store_set_default_engine_none_clears(tmp_path: Path) -> None:
    """Setting engine to None clears it."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_default_engine(room_id, "claude")
    await store.set_default_engine(room_id, None)
    result = await store.get_default_engine(room_id)

    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_get_trigger_mode_default(tmp_path: Path) -> None:
    """get_trigger_mode returns None for unknown room (represents 'all')."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    result = await store.get_trigger_mode("!unknown:example.org")
    # None represents 'all' (the default)
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_set_and_get_trigger_mode(tmp_path: Path) -> None:
    """set_trigger_mode stores and get_trigger_mode retrieves."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_trigger_mode(room_id, "mentions")
    result = await store.get_trigger_mode(room_id)

    assert result == "mentions"


@pytest.mark.anyio
async def test_room_prefs_store_set_trigger_mode_all_clears(tmp_path: Path) -> None:
    """Setting trigger mode to 'all' clears stored value (returns None)."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_trigger_mode(room_id, "mentions")
    await store.set_trigger_mode(room_id, "all")
    result = await store.get_trigger_mode(room_id)

    # 'all' is the default, stored as None
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_persistence(tmp_path: Path) -> None:
    """Changes persist to disk and reload."""
    config_path = tmp_path / "config.toml"
    room_id = "!room:example.org"

    # Create store and set values
    store1 = RoomPrefsStore(config_path)
    await store1.set_default_engine(room_id, "claude")
    await store1.set_trigger_mode(room_id, "mentions")

    # Create new store - should load persisted values
    store2 = RoomPrefsStore(config_path)
    assert await store2.get_default_engine(room_id) == "claude"
    assert await store2.get_trigger_mode(room_id) == "mentions"


@pytest.mark.anyio
async def test_room_prefs_store_multiple_rooms(tmp_path: Path) -> None:
    """Store handles multiple rooms independently."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)

    room1 = "!room1:example.org"
    room2 = "!room2:example.org"

    await store.set_default_engine(room1, "claude")
    await store.set_default_engine(room2, "codex")
    await store.set_trigger_mode(room1, "mentions")

    assert await store.get_default_engine(room1) == "claude"
    assert await store.get_default_engine(room2) == "codex"
    assert await store.get_trigger_mode(room1) == "mentions"
    assert await store.get_trigger_mode(room2) is None  # Default (all)


# --- Engine overrides tests ---


@pytest.mark.anyio
async def test_room_prefs_store_get_engine_override_default(tmp_path: Path) -> None:
    """get_engine_override returns None for unknown room/engine."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    result = await store.get_engine_override("!unknown:example.org", "codex")
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_set_and_get_engine_override(tmp_path: Path) -> None:
    """set_engine_override stores and get_engine_override retrieves."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    from takopi_matrix.engine_overrides import EngineOverrides

    override = EngineOverrides(model="gpt-4", reasoning="high")
    await store.set_engine_override(room_id, "codex", override)
    result = await store.get_engine_override(room_id, "codex")

    assert result is not None
    assert result.model == "gpt-4"
    assert result.reasoning == "high"


@pytest.mark.anyio
async def test_room_prefs_store_update_engine_override(tmp_path: Path) -> None:
    """Engine overrides can be updated."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    from takopi_matrix.engine_overrides import EngineOverrides

    # Set initial
    await store.set_engine_override(room_id, "codex", EngineOverrides(model="gpt-4"))

    # Update with new overrides
    await store.set_engine_override(
        room_id, "codex", EngineOverrides(model="gpt-4", reasoning="medium")
    )
    result = await store.get_engine_override(room_id, "codex")

    assert result is not None
    assert result.model == "gpt-4"
    assert result.reasoning == "medium"


@pytest.mark.anyio
async def test_room_prefs_store_clear_engine_override(tmp_path: Path) -> None:
    """Clearing engine override removes it."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    from takopi_matrix.engine_overrides import EngineOverrides

    await store.set_engine_override(room_id, "codex", EngineOverrides(model="gpt-4"))
    await store.clear_engine_override(room_id, "codex")

    result = await store.get_engine_override(room_id, "codex")
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_multiple_engine_overrides(tmp_path: Path) -> None:
    """Multiple engines can have overrides in same room."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    from takopi_matrix.engine_overrides import EngineOverrides

    await store.set_engine_override(room_id, "codex", EngineOverrides(model="gpt-4"))
    await store.set_engine_override(
        room_id, "claude", EngineOverrides(model="opus", reasoning="high")
    )

    codex_result = await store.get_engine_override(room_id, "codex")
    claude_result = await store.get_engine_override(room_id, "claude")

    assert codex_result is not None
    assert codex_result.model == "gpt-4"
    assert claude_result is not None
    assert claude_result.model == "opus"
    assert claude_result.reasoning == "high"


# --- Edge cases ---


@pytest.mark.anyio
async def test_room_prefs_store_special_room_ids(tmp_path: Path) -> None:
    """Store handles special characters in room IDs."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)

    room_id = "!abc123:matrix.example.org"
    await store.set_default_engine(room_id, "claude")
    assert await store.get_default_engine(room_id) == "claude"


@pytest.mark.anyio
async def test_room_prefs_store_empty_string_engine(tmp_path: Path) -> None:
    """Empty string engine is treated as None."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_default_engine(room_id, "claude")
    await store.set_default_engine(room_id, "")  # Empty string
    result = await store.get_default_engine(room_id)

    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_get_all_rooms(tmp_path: Path) -> None:
    """get_all_rooms returns all rooms with engines."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)

    await store.set_default_engine("!room1:example.org", "claude")
    await store.set_default_engine("!room2:example.org", "codex")

    all_rooms = await store.get_all_rooms()

    assert "!room1:example.org" in all_rooms
    assert "!room2:example.org" in all_rooms
    assert all_rooms["!room1:example.org"] == "claude"
    assert all_rooms["!room2:example.org"] == "codex"


@pytest.mark.anyio
async def test_room_prefs_store_clear_default_engine(tmp_path: Path) -> None:
    """clear_default_engine clears the engine."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_default_engine(room_id, "claude")
    await store.clear_default_engine(room_id)
    result = await store.get_default_engine(room_id)

    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_clear_trigger_mode(tmp_path: Path) -> None:
    """clear_trigger_mode clears the mode."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_trigger_mode(room_id, "mentions")
    await store.clear_trigger_mode(room_id)
    result = await store.get_trigger_mode(room_id)

    assert result is None


@pytest.mark.anyio
async def test_room_prefs_store_engine_override_normalizes_engine(
    tmp_path: Path,
) -> None:
    """Engine ID is normalized when setting/getting overrides."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    from takopi_matrix.engine_overrides import EngineOverrides

    # Set with uppercase
    await store.set_engine_override(room_id, "CODEX", EngineOverrides(model="gpt-4"))

    # Get with lowercase
    result = await store.get_engine_override(room_id, "codex")
    assert result is not None
    assert result.model == "gpt-4"


@pytest.mark.anyio
async def test_room_prefs_store_engine_override_empty_engine(tmp_path: Path) -> None:
    """Empty engine string is ignored."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    from takopi_matrix.engine_overrides import EngineOverrides

    # Set with empty engine - should be no-op
    await store.set_engine_override(room_id, "", EngineOverrides(model="gpt-4"))

    # Get with empty engine - should return None
    result = await store.get_engine_override(room_id, "")
    assert result is None


# --- Additional edge case tests for uncovered lines ---


@pytest.mark.anyio
async def test_room_prefs_v1_migration_string_format(tmp_path: Path) -> None:
    """v1 migration handles old string format (room_id -> engine directly)."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write v1 format with string values (old format)
    v1_state = {
        "version": 1,
        "rooms": {
            "!room1:example.org": "opus",  # Old format: string value
            "!room2:example.org": "sonnet",
        },
    }
    state_path.write_text(json.dumps(v1_state))

    # Pass state_path directly - RoomPrefsStore expects the state file path
    store = RoomPrefsStore(state_path)

    # Should migrate string values correctly
    engine1 = await store.get_default_engine("!room1:example.org")
    engine2 = await store.get_default_engine("!room2:example.org")
    assert engine1 == "opus"
    assert engine2 == "sonnet"


@pytest.mark.anyio
async def test_room_prefs_get_override_invalid_overrides_dict_type(
    tmp_path: Path,
) -> None:
    """get_engine_override returns None if engine_overrides is not a dict."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write invalid state with engine_overrides as string
    state = {
        "version": 2,
        "rooms": {
            "!room:example.org": {
                "default_engine": "opus",
                "trigger_mode": None,
                "engine_overrides": "invalid",  # Should be a dict
            },
        },
    }
    state_path.write_text(json.dumps(state))

    store = RoomPrefsStore(state_path)
    result = await store.get_engine_override("!room:example.org", "opus")
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_get_override_invalid_override_data_type(
    tmp_path: Path,
) -> None:
    """get_engine_override returns None if override data is not a dict."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write invalid state with override data as string
    state = {
        "version": 2,
        "rooms": {
            "!room:example.org": {
                "default_engine": "opus",
                "trigger_mode": None,
                "engine_overrides": {
                    "opus": "invalid",  # Should be a dict like {"model": "...", "reasoning": "..."}
                },
            },
        },
    }
    state_path.write_text(json.dumps(state))

    store = RoomPrefsStore(state_path)
    result = await store.get_engine_override("!room:example.org", "opus")
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_get_override_invalid_model_type(tmp_path: Path) -> None:
    """get_engine_override returns None model if model is not a string."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write state with non-string model
    state = {
        "version": 2,
        "rooms": {
            "!room:example.org": {
                "default_engine": None,
                "trigger_mode": None,
                "engine_overrides": {
                    "opus": {"model": 123, "reasoning": "medium"},  # model should be string
                },
            },
        },
    }
    state_path.write_text(json.dumps(state))

    store = RoomPrefsStore(state_path)
    result = await store.get_engine_override("!room:example.org", "opus")
    # Should return override but with model set to None
    assert result is not None
    assert result.model is None
    assert result.reasoning == "medium"


@pytest.mark.anyio
async def test_room_prefs_get_override_invalid_reasoning_type(tmp_path: Path) -> None:
    """get_engine_override returns None reasoning if reasoning is not a string."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write state with non-string reasoning
    state = {
        "version": 2,
        "rooms": {
            "!room:example.org": {
                "default_engine": None,
                "trigger_mode": None,
                "engine_overrides": {
                    "opus": {"model": "gpt-4", "reasoning": 456},  # reasoning should be string
                },
            },
        },
    }
    state_path.write_text(json.dumps(state))

    store = RoomPrefsStore(state_path)
    result = await store.get_engine_override("!room:example.org", "opus")
    # Should return override but with reasoning set to None
    assert result is not None
    assert result.model == "gpt-4"
    assert result.reasoning is None


@pytest.mark.anyio
async def test_room_prefs_clear_override_nonexistent_room(tmp_path: Path) -> None:
    """Clearing override for non-existent room is no-op."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)

    # Should not raise
    await store.clear_engine_override("!nonexistent:example.org", "opus")

    result = await store.get_engine_override("!nonexistent:example.org", "opus")
    assert result is None


@pytest.mark.anyio
async def test_room_prefs_set_override_creates_overrides_dict(tmp_path: Path) -> None:
    """set_engine_override creates engine_overrides dict if missing."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write state with room but no engine_overrides key
    state = {
        "version": 2,
        "rooms": {
            "!room:example.org": {
                "default_engine": "opus",
                "trigger_mode": None,
                # Missing: "engine_overrides"
            },
        },
    }
    state_path.write_text(json.dumps(state))

    store = RoomPrefsStore(state_path)

    from takopi_matrix.engine_overrides import EngineOverrides

    await store.set_engine_override("!room:example.org", "opus", EngineOverrides(model="gpt-4"))

    result = await store.get_engine_override("!room:example.org", "opus")
    assert result is not None
    assert result.model == "gpt-4"


@pytest.mark.anyio
async def test_room_is_not_empty_with_engine(tmp_path: Path) -> None:
    """Room with only default engine is not empty."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_default_engine(room_id, "opus")

    # Clearing trigger mode shouldn't remove the room since engine is set
    await store.set_trigger_mode(room_id, None)

    # Room should still exist with engine
    result = await store.get_default_engine(room_id)
    assert result == "opus"


@pytest.mark.anyio
async def test_room_is_not_empty_with_trigger_mode(tmp_path: Path) -> None:
    """Room with only trigger mode is not empty."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)
    room_id = "!room:example.org"

    await store.set_trigger_mode(room_id, "mentions")

    # Room should exist with trigger mode
    result = await store.get_trigger_mode(room_id)
    assert result == "mentions"


@pytest.mark.anyio
async def test_has_engine_overrides_skips_non_dict(tmp_path: Path) -> None:
    """_has_engine_overrides skips non-dict override values."""
    import json

    state_path = tmp_path / "matrix_room_prefs_state.json"

    # Write state with mixed override types
    state = {
        "version": 2,
        "rooms": {
            "!room:example.org": {
                "default_engine": None,
                "trigger_mode": None,
                "engine_overrides": {
                    "invalid": "not a dict",  # Should be skipped
                    "valid": {"model": "gpt-4", "reasoning": None},
                },
            },
        },
    }
    state_path.write_text(json.dumps(state))

    store = RoomPrefsStore(state_path)

    # Valid override should still be accessible
    result = await store.get_engine_override("!room:example.org", "valid")
    assert result is not None
    assert result.model == "gpt-4"


@pytest.mark.anyio
async def test_remove_room_nonexistent(tmp_path: Path) -> None:
    """Removing non-existent room is no-op."""
    config_path = tmp_path / "config.toml"
    store = RoomPrefsStore(config_path)

    # Set something in one room
    await store.set_default_engine("!room1:example.org", "opus")

    # Clear a different room (that doesn't exist)
    await store.set_default_engine("!nonexistent:example.org", None)

    # Original room should still exist
    result = await store.get_default_engine("!room1:example.org")
    assert result == "opus"
