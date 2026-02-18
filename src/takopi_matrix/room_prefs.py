"""Per-room engine preferences store for Matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from takopi.api import RunContext, get_logger

from .engine_overrides import EngineOverrides, normalize_overrides
from .state_store import JsonStateStore

logger = get_logger(__name__)

STATE_VERSION = 3
STATE_FILENAME = "matrix_room_prefs_state.json"


@dataclass
class _RoomPrefs:
    """Preferences for a single room."""

    default_engine: str | None = None
    context_project: str | None = None
    context_branch: str | None = None
    trigger_mode: str | None = None
    engine_overrides: dict[str, dict[str, str | None]] = field(default_factory=dict)


@dataclass
class _RoomPrefsState:
    """Root state containing all room preferences."""

    version: int
    rooms: dict[str, dict[str, Any]] = field(default_factory=dict)


def resolve_prefs_path(config_path: Path) -> Path:
    """Get the path for room prefs state file, adjacent to config."""
    return config_path.with_name(STATE_FILENAME)


def _room_key(room_id: str) -> str:
    """Normalize room ID to use as dict key."""
    return room_id


def _normalize_text(value: str | None) -> str | None:
    """Normalize text value, returning None for empty strings."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_trigger_mode(value: str | None) -> str | None:
    """Normalize trigger mode value.

    Returns 'mentions' if valid, None otherwise (default is 'all').
    """
    if value is None:
        return None
    value = value.strip().lower()
    if value == "mentions":
        return "mentions"
    # 'all' is the default, so we store None
    return None


def _normalize_engine_id(value: str | None) -> str | None:
    """Normalize engine ID to lowercase."""
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def _new_state() -> _RoomPrefsState:
    """Create a new empty state."""
    return _RoomPrefsState(version=STATE_VERSION, rooms={})


class RoomPrefsStore(JsonStateStore[_RoomPrefsState]):
    """Store for per-room engine preferences.

    Stores default engine assignments for each Matrix room.
    File is hot-reloaded when modified externally.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            path,
            version=STATE_VERSION,
            state_type=_RoomPrefsState,
            state_factory=_new_state,
            log_prefix="matrix.room_prefs",
        )

    def _migrate_state(self, data: dict[str, Any], from_version: int) -> dict[str, Any]:
        """Migrate room prefs from older versions.

        v1 -> v2: Add trigger_mode and engine_overrides fields to each room.
        v2 -> v3: Add context_project and context_branch fields to each room.
        """
        migrated = data
        current_version = from_version

        if current_version == 1:
            rooms = data.get("rooms", {})
            migrated_rooms: dict[str, dict[str, Any]] = {}
            for room_id, room_data in rooms.items():
                if isinstance(room_data, dict):
                    migrated_rooms[room_id] = {
                        "default_engine": room_data.get("default_engine"),
                        "trigger_mode": None,  # New field, default to 'all'
                        "engine_overrides": {},  # New field, empty dict
                    }
                elif isinstance(room_data, str):
                    # Old format: room_id -> engine_name directly
                    migrated_rooms[room_id] = {
                        "default_engine": room_data,
                        "trigger_mode": None,
                        "engine_overrides": {},
                    }
            logger.info(
                "matrix.room_prefs.migration_v1_v2",
                rooms_migrated=len(migrated_rooms),
            )
            migrated = {"version": 2, "rooms": migrated_rooms}
            current_version = 2

        if current_version == 2:
            rooms = migrated.get("rooms", {})
            if isinstance(rooms, dict):
                for room_data in rooms.values():
                    if not isinstance(room_data, dict):
                        continue
                    room_data.setdefault("context_project", None)
                    room_data.setdefault("context_branch", None)
                logger.info(
                    "matrix.room_prefs.migration_v2_v3",
                    rooms_migrated=len(rooms),
                )
            migrated["version"] = 3
            return migrated

        # Unknown migration path
        return migrated

    # --- Room Context ---

    async def get_context(self, room_id: str) -> RunContext | None:
        """Get the bound context for a room."""
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if room is None:
                return None
            project = _normalize_text(room.get("context_project"))
            branch = _normalize_text(room.get("context_branch"))
            if project is None:
                return None
            return RunContext(project=project, branch=branch)

    async def set_context(self, room_id: str, context: RunContext | None) -> None:
        """Set or clear the bound context for a room."""
        project = _normalize_text(context.project) if context is not None else None
        branch = _normalize_text(context.branch) if context is not None else None
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if project is None:
                if room is None:
                    return
                room["context_project"] = None
                room["context_branch"] = None
                if self._room_is_empty(room):
                    self._remove_room_locked(room_id)
                self._save_locked()
                return
            room = self._ensure_room_locked(room_id)
            room["context_project"] = project
            room["context_branch"] = branch
            self._save_locked()

    async def clear_context(self, room_id: str) -> None:
        """Clear room-bound context."""
        await self.set_context(room_id, None)

    # --- Default Engine ---

    async def get_default_engine(self, room_id: str) -> str | None:
        """Get the default engine for a room, or None if not set."""
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if room is None:
                return None
            return _normalize_text(room.get("default_engine"))

    async def set_default_engine(self, room_id: str, engine: str | None) -> None:
        """Set the default engine for a room, or clear if engine is None."""
        normalized = _normalize_text(engine)
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if normalized is None:
                if room is None:
                    return
                room["default_engine"] = None
                if self._room_is_empty(room):
                    self._remove_room_locked(room_id)
                self._save_locked()
                return
            room = self._ensure_room_locked(room_id)
            room["default_engine"] = normalized
            self._save_locked()

    async def clear_default_engine(self, room_id: str) -> None:
        """Clear the default engine for a room."""
        await self.set_default_engine(room_id, None)

    # --- Trigger Mode ---

    async def get_trigger_mode(self, room_id: str) -> str | None:
        """Get the trigger mode for a room ('mentions' or None for 'all')."""
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if room is None:
                return None
            return _normalize_trigger_mode(room.get("trigger_mode"))

    async def set_trigger_mode(self, room_id: str, mode: str | None) -> None:
        """Set the trigger mode for a room.

        Args:
            room_id: The Matrix room ID.
            mode: 'mentions' to only respond to mentions, 'all' or None for default.
        """
        normalized = _normalize_trigger_mode(mode)
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if normalized is None:
                if room is None:
                    return
                room["trigger_mode"] = None
                if self._room_is_empty(room):
                    self._remove_room_locked(room_id)
                self._save_locked()
                return
            room = self._ensure_room_locked(room_id)
            room["trigger_mode"] = normalized
            self._save_locked()

    async def clear_trigger_mode(self, room_id: str) -> None:
        """Clear the trigger mode for a room (resets to 'all')."""
        await self.set_trigger_mode(room_id, None)

    # --- Engine Overrides ---

    async def get_engine_override(
        self, room_id: str, engine: str
    ) -> EngineOverrides | None:
        """Get engine-specific overrides (model, reasoning) for a room.

        Args:
            room_id: The Matrix room ID.
            engine: The engine ID to get overrides for.

        Returns:
            EngineOverrides if any are set, None otherwise.
        """
        engine_key = _normalize_engine_id(engine)
        if engine_key is None:
            return None
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if room is None:
                return None
            overrides_dict = room.get("engine_overrides", {})
            if not isinstance(overrides_dict, dict):
                return None
            override_data = overrides_dict.get(engine_key)
            if override_data is None or not isinstance(override_data, dict):
                return None

            # Type-safe extraction with validation
            model = override_data.get("model")
            reasoning = override_data.get("reasoning")

            # Validate types - JSON could contain unexpected types
            if model is not None and not isinstance(model, str):
                logger.warning(
                    "matrix.room_prefs.invalid_model_type",
                    room_id=room_id,
                    engine=engine_key,
                    value_type=type(model).__name__,
                )
                model = None
            if reasoning is not None and not isinstance(reasoning, str):
                logger.warning(
                    "matrix.room_prefs.invalid_reasoning_type",
                    room_id=room_id,
                    engine=engine_key,
                    value_type=type(reasoning).__name__,
                )
                reasoning = None

            override = EngineOverrides(model=model, reasoning=reasoning)
            return normalize_overrides(override)

    async def set_engine_override(
        self, room_id: str, engine: str, override: EngineOverrides | None
    ) -> None:
        """Set engine-specific overrides for a room.

        Args:
            room_id: The Matrix room ID.
            engine: The engine ID to set overrides for.
            override: The overrides to set, or None to clear.
        """
        engine_key = _normalize_engine_id(engine)
        if engine_key is None:
            return
        normalized = normalize_overrides(override)
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._get_room_locked(room_id)
            if normalized is None:
                if room is None:
                    return
                overrides_dict = room.get("engine_overrides", {})
                if isinstance(overrides_dict, dict):
                    overrides_dict.pop(engine_key, None)
                if self._room_is_empty(room):
                    self._remove_room_locked(room_id)
                self._save_locked()
                return
            room = self._ensure_room_locked(room_id)
            if "engine_overrides" not in room or not isinstance(
                room.get("engine_overrides"), dict
            ):
                room["engine_overrides"] = {}
            room["engine_overrides"][engine_key] = {
                "model": normalized.model,
                "reasoning": normalized.reasoning,
            }
            self._save_locked()

    async def clear_engine_override(self, room_id: str, engine: str) -> None:
        """Clear engine-specific overrides for a room."""
        await self.set_engine_override(room_id, engine, None)

    # --- Utility Methods ---

    async def get_all_rooms(self) -> dict[str, str | None]:
        """Get all rooms with their default engines."""
        async with self._lock:
            self._reload_locked_if_needed()
            return {
                room_id: prefs.get("default_engine")
                for room_id, prefs in self._state.rooms.items()
            }

    # --- Internal Methods ---

    def _get_room_locked(self, room_id: str) -> dict[str, Any] | None:
        """Get room prefs dict, or None if room not in state."""
        return self._state.rooms.get(_room_key(room_id))

    def _ensure_room_locked(self, room_id: str) -> dict[str, Any]:
        """Get or create room prefs dict."""
        key = _room_key(room_id)
        entry = self._state.rooms.get(key)
        if entry is not None:
            return entry
        entry = {
            "default_engine": None,
            "context_project": None,
            "context_branch": None,
            "trigger_mode": None,
            "engine_overrides": {},
        }
        self._state.rooms[key] = entry
        return entry

    def _room_is_empty(self, room: dict[str, Any]) -> bool:
        """Check if a room has no meaningful preferences set.

        A room is considered empty if all of the following are None/empty:
        - default_engine
        - trigger_mode
        - engine_overrides

        Empty rooms are candidates for removal to keep state file clean.
        """
        if _normalize_text(room.get("default_engine")) is not None:
            return False
        if _normalize_text(room.get("context_project")) is not None:
            return False
        if _normalize_text(room.get("context_branch")) is not None:
            return False
        if _normalize_trigger_mode(room.get("trigger_mode")) is not None:
            return False
        overrides = room.get("engine_overrides", {})
        return not (
            isinstance(overrides, dict) and self._has_engine_overrides(overrides)
        )

    @staticmethod
    def _has_engine_overrides(overrides: dict[str, Any]) -> bool:
        """Check if there are any non-empty engine overrides."""
        for override_data in overrides.values():
            if not isinstance(override_data, dict):
                continue

            model = override_data.get("model")
            if model is not None and not isinstance(model, str):
                model = None

            reasoning = override_data.get("reasoning")
            if reasoning is not None and not isinstance(reasoning, str):
                reasoning = None

            override = EngineOverrides(
                model=model,
                reasoning=reasoning,
            )
            if normalize_overrides(override) is not None:
                return True
        return False

    def _remove_room_locked(self, room_id: str) -> bool:
        """Remove room from state. Returns True if room was present."""
        key = _room_key(room_id)
        if key not in self._state.rooms:
            return False
        del self._state.rooms[key]
        return True
