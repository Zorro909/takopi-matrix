"""Per-room engine preferences store for Matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from takopi.logging import get_logger

from .state_store import JsonStateStore

logger = get_logger(__name__)

STATE_VERSION = 1
STATE_FILENAME = "matrix_room_prefs_state.json"


@dataclass
class _RoomPrefs:
    """Preferences for a single room."""

    default_engine: str | None = None


@dataclass
class _RoomPrefsState:
    """Root state containing all room preferences."""

    version: int
    rooms: dict[str, dict[str, str | None]] = field(default_factory=dict)


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
            if normalized is None:
                if self._remove_room_locked(room_id):
                    self._save_locked()
                return
            room = self._ensure_room_locked(room_id)
            room["default_engine"] = normalized
            self._save_locked()

    async def clear_default_engine(self, room_id: str) -> None:
        """Clear the default engine for a room."""
        await self.set_default_engine(room_id, None)

    async def get_all_rooms(self) -> dict[str, str | None]:
        """Get all rooms with their default engines."""
        async with self._lock:
            self._reload_locked_if_needed()
            return {
                room_id: prefs.get("default_engine")
                for room_id, prefs in self._state.rooms.items()
            }

    def _get_room_locked(self, room_id: str) -> dict[str, str | None] | None:
        """Get room prefs dict, or None if room not in state."""
        return self._state.rooms.get(_room_key(room_id))

    def _ensure_room_locked(self, room_id: str) -> dict[str, str | None]:
        """Get or create room prefs dict."""
        key = _room_key(room_id)
        entry = self._state.rooms.get(key)
        if entry is not None:
            return entry
        entry = {"default_engine": None}
        self._state.rooms[key] = entry
        return entry

    def _remove_room_locked(self, room_id: str) -> bool:
        """Remove room from state. Returns True if room was present."""
        key = _room_key(room_id)
        if key not in self._state.rooms:
            return False
        del self._state.rooms[key]
        return True
