"""Engine overrides for Matrix transport.

Provides per-room model and reasoning level overrides, with layered resolution
(thread override -> room default -> global default).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OverrideSource = Literal["thread_override", "room_default", "default"]

REASONING_LEVELS: tuple[str, ...] = ("minimal", "low", "medium", "high", "xhigh")
REASONING_SUPPORTED_ENGINES = frozenset({"codex"})


@dataclass(frozen=True, slots=True)
class EngineOverrides:
    """Engine-specific overrides (model, reasoning level)."""

    model: str | None = None
    reasoning: str | None = None


@dataclass(frozen=True, slots=True)
class OverrideValueResolution:
    """Result of resolving an override value through the hierarchy."""

    value: str | None
    source: OverrideSource
    thread_value: str | None
    room_value: str | None


def normalize_override_value(value: str | None) -> str | None:
    """Normalize an override value, returning None for empty strings."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def normalize_overrides(overrides: EngineOverrides | None) -> EngineOverrides | None:
    """Normalize an EngineOverrides, returning None if all fields are empty."""
    if overrides is None:
        return None
    model = normalize_override_value(overrides.model)
    reasoning = normalize_override_value(overrides.reasoning)
    if model is None and reasoning is None:
        return None
    return EngineOverrides(model=model, reasoning=reasoning)


def merge_overrides(
    thread_override: EngineOverrides | None,
    room_override: EngineOverrides | None,
) -> EngineOverrides | None:
    """Merge thread and room overrides, with thread taking precedence.

    Args:
        thread_override: Override from thread (if in a thread context).
        room_override: Override from room default.

    Returns:
        Merged EngineOverrides, or None if no overrides are set.
    """
    thread = normalize_overrides(thread_override)
    room = normalize_overrides(room_override)
    if thread is None and room is None:
        return None
    model = None
    reasoning = None
    if thread is not None and thread.model is not None:
        model = thread.model
    elif room is not None:
        model = room.model
    if thread is not None and thread.reasoning is not None:
        reasoning = thread.reasoning
    elif room is not None:
        reasoning = room.reasoning
    return normalize_overrides(EngineOverrides(model=model, reasoning=reasoning))


def resolve_override_value(
    *,
    thread_override: EngineOverrides | None,
    room_override: EngineOverrides | None,
    field: Literal["model", "reasoning"],
) -> OverrideValueResolution:
    """Resolve a specific override field through the hierarchy.

    Resolution order:
    1. Thread override (if in thread context)
    2. Room default
    3. Global default (None)

    Args:
        thread_override: Override from thread.
        room_override: Override from room.
        field: Which field to resolve ("model" or "reasoning").

    Returns:
        OverrideValueResolution with the resolved value and source.
    """
    thread_value = normalize_override_value(
        getattr(thread_override, field, None) if thread_override is not None else None
    )
    room_value = normalize_override_value(
        getattr(room_override, field, None) if room_override is not None else None
    )
    if thread_value is not None:
        return OverrideValueResolution(
            value=thread_value,
            source="thread_override",
            thread_value=thread_value,
            room_value=room_value,
        )
    if room_value is not None:
        return OverrideValueResolution(
            value=room_value,
            source="room_default",
            thread_value=thread_value,
            room_value=room_value,
        )
    return OverrideValueResolution(
        value=None,
        source="default",
        thread_value=thread_value,
        room_value=room_value,
    )


def allowed_reasoning_levels(engine: str) -> tuple[str, ...]:
    """Get allowed reasoning levels for an engine."""
    _ = engine
    return REASONING_LEVELS


def supports_reasoning(engine: str) -> bool:
    """Check if an engine supports reasoning level configuration."""
    return engine in REASONING_SUPPORTED_ENGINES
