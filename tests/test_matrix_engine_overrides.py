"""Tests for engine overrides functionality."""

from __future__ import annotations

import pytest

from takopi_matrix.engine_overrides import (
    REASONING_LEVELS,
    REASONING_SUPPORTED_ENGINES,
    EngineOverrides,
    allowed_reasoning_levels,
    merge_overrides,
    normalize_override_value,
    normalize_overrides,
    resolve_override_value,
    supports_reasoning,
)


class TestNormalizeOverrideValue:
    def test_none_returns_none(self) -> None:
        """None input returns None."""
        assert normalize_override_value(None) is None

    def test_empty_string_returns_none(self) -> None:
        """Empty string returns None."""
        assert normalize_override_value("") is None

    def test_whitespace_only_returns_none(self) -> None:
        """Whitespace-only string returns None."""
        assert normalize_override_value("   ") is None

    def test_strips_whitespace(self) -> None:
        """Whitespace is stripped from values."""
        assert normalize_override_value("  gpt-4  ") == "gpt-4"

    def test_preserves_valid_value(self) -> None:
        """Valid values are preserved."""
        assert normalize_override_value("claude-3-opus") == "claude-3-opus"


class TestNormalizeOverrides:
    def test_none_returns_none(self) -> None:
        """None input returns None."""
        assert normalize_overrides(None) is None

    def test_empty_overrides_returns_none(self) -> None:
        """Overrides with all None fields returns None."""
        override = EngineOverrides(model=None, reasoning=None)
        assert normalize_overrides(override) is None

    def test_whitespace_only_fields_returns_none(self) -> None:
        """Overrides with whitespace-only fields returns None."""
        override = EngineOverrides(model="  ", reasoning="   ")
        assert normalize_overrides(override) is None

    def test_normalizes_model(self) -> None:
        """Model field is normalized."""
        override = EngineOverrides(model="  gpt-4  ", reasoning=None)
        result = normalize_overrides(override)
        assert result is not None
        assert result.model == "gpt-4"
        assert result.reasoning is None

    def test_normalizes_reasoning(self) -> None:
        """Reasoning field is normalized."""
        override = EngineOverrides(model=None, reasoning="  high  ")
        result = normalize_overrides(override)
        assert result is not None
        assert result.model is None
        assert result.reasoning == "high"

    def test_normalizes_both_fields(self) -> None:
        """Both fields are normalized."""
        override = EngineOverrides(model="  gpt-4  ", reasoning="  high  ")
        result = normalize_overrides(override)
        assert result is not None
        assert result.model == "gpt-4"
        assert result.reasoning == "high"


class TestMergeOverrides:
    def test_both_none_returns_none(self) -> None:
        """Both None returns None."""
        assert merge_overrides(None, None) is None

    def test_thread_only_returns_thread(self) -> None:
        """Only thread override returns thread values."""
        thread = EngineOverrides(model="gpt-4", reasoning=None)
        result = merge_overrides(thread, None)
        assert result is not None
        assert result.model == "gpt-4"

    def test_room_only_returns_room(self) -> None:
        """Only room override returns room values."""
        room = EngineOverrides(model="claude-3", reasoning="high")
        result = merge_overrides(None, room)
        assert result is not None
        assert result.model == "claude-3"
        assert result.reasoning == "high"

    def test_thread_takes_precedence(self) -> None:
        """Thread override takes precedence over room."""
        thread = EngineOverrides(model="gpt-4", reasoning=None)
        room = EngineOverrides(model="claude-3", reasoning="high")
        result = merge_overrides(thread, room)
        assert result is not None
        assert result.model == "gpt-4"  # Thread wins
        assert result.reasoning == "high"  # Falls back to room

    def test_partial_thread_falls_back_to_room(self) -> None:
        """Thread with partial values falls back to room for missing fields."""
        thread = EngineOverrides(model=None, reasoning="low")
        room = EngineOverrides(model="claude-3", reasoning="high")
        result = merge_overrides(thread, room)
        assert result is not None
        assert result.model == "claude-3"  # Falls back to room
        assert result.reasoning == "low"  # Thread wins


class TestResolveOverrideValue:
    def test_thread_takes_precedence(self) -> None:
        """Thread value takes precedence."""
        thread = EngineOverrides(model="gpt-4", reasoning=None)
        room = EngineOverrides(model="claude-3", reasoning=None)
        result = resolve_override_value(
            thread_override=thread,
            room_override=room,
            field="model",
        )
        assert result.value == "gpt-4"
        assert result.source == "thread_override"

    def test_room_fallback(self) -> None:
        """Room value used when thread is None."""
        thread = EngineOverrides(model=None, reasoning=None)
        room = EngineOverrides(model="claude-3", reasoning=None)
        result = resolve_override_value(
            thread_override=thread,
            room_override=room,
            field="model",
        )
        assert result.value == "claude-3"
        assert result.source == "room_default"

    def test_default_when_both_none(self) -> None:
        """Returns None with default source when both are None."""
        result = resolve_override_value(
            thread_override=None,
            room_override=None,
            field="model",
        )
        assert result.value is None
        assert result.source == "default"

    def test_resolution_includes_raw_values(self) -> None:
        """Resolution includes both raw values for debugging."""
        thread = EngineOverrides(model="gpt-4", reasoning=None)
        room = EngineOverrides(model="claude-3", reasoning=None)
        result = resolve_override_value(
            thread_override=thread,
            room_override=room,
            field="model",
        )
        assert result.thread_value == "gpt-4"
        assert result.room_value == "claude-3"


class TestHelperFunctions:
    def test_supports_reasoning_true(self) -> None:
        """Engines in REASONING_SUPPORTED_ENGINES return True."""
        for engine in REASONING_SUPPORTED_ENGINES:
            assert supports_reasoning(engine) is True

    def test_supports_reasoning_false(self) -> None:
        """Engines not in REASONING_SUPPORTED_ENGINES return False."""
        assert supports_reasoning("unknown-engine") is False

    def test_allowed_reasoning_levels(self) -> None:
        """Returns the tuple of reasoning levels."""
        levels = allowed_reasoning_levels()
        assert levels == REASONING_LEVELS
        assert "low" in levels
        assert "high" in levels


class TestEngineOverridesDataclass:
    def test_frozen(self) -> None:
        """EngineOverrides is frozen (immutable)."""
        override = EngineOverrides(model="gpt-4", reasoning="high")
        with pytest.raises(AttributeError):
            override.model = "claude-3"  # type: ignore[misc]

    def test_defaults_to_none(self) -> None:
        """Fields default to None."""
        override = EngineOverrides()
        assert override.model is None
        assert override.reasoning is None

    def test_equality(self) -> None:
        """Equality comparison works."""
        a = EngineOverrides(model="gpt-4", reasoning="high")
        b = EngineOverrides(model="gpt-4", reasoning="high")
        c = EngineOverrides(model="claude-3", reasoning="high")
        assert a == b
        assert a != c
