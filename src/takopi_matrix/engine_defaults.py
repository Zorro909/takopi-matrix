"""Engine resolution for Matrix messages.

Implements hierarchical engine selection:
1. Directive (@engine in message)
2. Room default (stored preference)
3. Project default (from room binding or context)
4. Global default (from takopi config)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from takopi.api import RunContext, TransportRuntime

from .room_prefs import RoomPrefsStore

if TYPE_CHECKING:
    from .room_projects import RoomProjectMap

EngineSource = Literal[
    "directive",
    "room_default",
    "project_default",
    "global_default",
]


@dataclass(frozen=True, slots=True)
class EngineResolution:
    """Result of engine resolution with source tracking.

    Attributes:
        engine: The resolved engine ID to use.
        source: Where the engine was determined from.
        room_default: The room's stored default engine, if any.
        project_default: The project's default engine, if any.
    """

    engine: str
    source: EngineSource
    room_default: str | None
    project_default: str | None


async def resolve_engine_for_message(
    *,
    runtime: TransportRuntime,
    context: RunContext | None,
    explicit_engine: str | None,
    room_id: str,
    room_prefs: RoomPrefsStore | None,
    room_project_map: RoomProjectMap | None = None,
) -> EngineResolution:
    """Resolve the engine to use for a message.

    Resolution order (first match wins):
    1. Explicit engine from directive (@engine sonnet)
    2. Room default (from room_prefs store)
    3. Project default (from room binding or context)
    4. Global default (from takopi config)

    Args:
        runtime: The transport runtime for accessing config.
        context: Optional run context with project/branch info.
        explicit_engine: Engine specified via directive, if any.
        room_id: The Matrix room ID.
        room_prefs: Optional room preferences store.
        room_project_map: Optional room-to-project mapping.

    Returns:
        EngineResolution with the selected engine and metadata.
    """
    # Fetch room default if store is available
    room_default = None
    if room_prefs is not None:
        room_default = await room_prefs.get_default_engine(room_id)

    # Get effective context: explicit context or room's bound project
    effective_context = context
    if effective_context is None and room_project_map is not None:
        effective_context = room_project_map.context_for_room(room_id)

    # Get project default from runtime using effective context
    project_default = runtime.project_default_engine(effective_context)

    # Resolution cascade
    if explicit_engine is not None:
        return EngineResolution(
            engine=explicit_engine,
            source="directive",
            room_default=room_default,
            project_default=project_default,
        )

    if room_default is not None:
        return EngineResolution(
            engine=room_default,
            source="room_default",
            room_default=room_default,
            project_default=project_default,
        )

    if project_default is not None:
        return EngineResolution(
            engine=project_default,
            source="project_default",
            room_default=room_default,
            project_default=project_default,
        )

    return EngineResolution(
        engine=runtime.default_engine,
        source="global_default",
        room_default=room_default,
        project_default=project_default,
    )


def _allowed_room_ids(
    configured_room_ids: list[str],
    runtime: TransportRuntime,
    room_project_map: RoomProjectMap | None = None,
) -> set[str]:
    """Build the set of allowed room IDs.

    Combines:
    - Explicitly configured room_ids
    - Project-bound room IDs (from room_projects config)

    Args:
        configured_room_ids: Room IDs from [transports.matrix].room_ids
        runtime: Transport runtime (unused, kept for API compatibility).
        room_project_map: Optional room-to-project mapping.

    Returns:
        Set of all allowed room IDs.
    """
    allowed = set(configured_room_ids)
    # Add project-bound room IDs
    if room_project_map is not None:
        allowed.update(room_project_map.project_room_ids())
    return allowed


def resolve_context_for_room(
    *,
    room_id: str,
    directive_context: RunContext | None,
    room_project_map: RoomProjectMap | None,
) -> RunContext | None:
    """Resolve the RunContext for a message in a room.

    Priority:
    1. Directive context (explicit @project/branch in message)
    2. Room's bound project (from room_projects config)

    Args:
        room_id: The Matrix room ID.
        directive_context: Context from message directives, if any.
        room_project_map: Optional room-to-project mapping.

    Returns:
        RunContext if context can be determined, None otherwise.
    """
    # Directive takes priority
    if directive_context is not None:
        return directive_context

    # Fall back to room's bound project
    if room_project_map is not None:
        return room_project_map.context_for_room(room_id)

    return None
