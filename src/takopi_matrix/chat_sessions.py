"""Per-room/per-sender chat session store for Matrix."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from takopi.api import ResumeToken

from .state_store import JsonStateStore

STATE_VERSION = 1
STATE_FILENAME = "matrix_chat_sessions_state.json"


@dataclass
class _ChatSessionsState:
    version: int
    cwd: str | None = None
    rooms: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)


def resolve_chat_sessions_path(config_path: Path) -> Path:
    """Get the path for chat sessions state file, adjacent to config."""
    return config_path.with_name(STATE_FILENAME)


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalize_engine_id(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower()
    return value or None


def _new_state() -> _ChatSessionsState:
    return _ChatSessionsState(version=STATE_VERSION, rooms={})


class MatrixChatSessionStore(JsonStateStore[_ChatSessionsState]):
    """Store resume tokens for non-thread Matrix messages.

    Scope: room_id + sender + engine.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            path,
            version=STATE_VERSION,
            state_type=_ChatSessionsState,
            state_factory=_new_state,
            log_prefix="matrix.chat_sessions",
        )

    async def get_session_resume(
        self, room_id: str, sender: str, engine: str
    ) -> ResumeToken | None:
        sender_key = _normalize_text(sender)
        engine_key = _normalize_engine_id(engine)
        if sender_key is None or engine_key is None:
            return None
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._state.rooms.get(room_id)
            if not isinstance(room, dict):
                return None
            sender_sessions = room.get(sender_key)
            if not isinstance(sender_sessions, dict):
                return None
            resume = sender_sessions.get(engine_key)
            if not isinstance(resume, str) or not resume:
                return None
            return ResumeToken(engine=engine_key, value=resume)

    async def sync_startup_cwd(self, cwd: Path) -> bool:
        normalized = str(cwd.expanduser().resolve())
        async with self._lock:
            self._reload_locked_if_needed()
            previous = self._state.cwd
            cleared = False
            if previous is not None and previous != normalized:
                self._state.rooms = {}
                cleared = True
            if previous != normalized:
                self._state.cwd = normalized
                self._save_locked()
            return cleared

    async def set_session_resume(
        self, room_id: str, sender: str, token: ResumeToken
    ) -> None:
        sender_key = _normalize_text(sender)
        engine_key = _normalize_engine_id(token.engine)
        resume_value = _normalize_text(token.value)
        if sender_key is None or engine_key is None or resume_value is None:
            return
        async with self._lock:
            self._reload_locked_if_needed()
            if self._state.cwd is None:
                self._state.cwd = str(Path.cwd().expanduser().resolve())
            room = self._ensure_room_locked(room_id)
            sender_sessions = room.get(sender_key)
            if not isinstance(sender_sessions, dict):
                sender_sessions = {}
                room[sender_key] = sender_sessions
            sender_sessions[engine_key] = resume_value
            self._save_locked()

    async def clear_sessions(self, room_id: str, sender: str) -> None:
        sender_key = _normalize_text(sender)
        if sender_key is None:
            return
        async with self._lock:
            self._reload_locked_if_needed()
            room = self._state.rooms.get(room_id)
            if not isinstance(room, dict):
                return
            room.pop(sender_key, None)
            if not room:
                self._state.rooms.pop(room_id, None)
            self._save_locked()

    def _ensure_room_locked(self, room_id: str) -> dict[str, dict[str, str]]:
        room = self._state.rooms.get(room_id)
        if isinstance(room, dict):
            return room
        room = {}
        self._state.rooms[room_id] = room
        return room
