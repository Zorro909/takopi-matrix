"""Generic JSON state store with file locking and hot-reload."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import anyio

from takopi.logging import get_logger

logger = get_logger(__name__)


class _VersionedState(Protocol):
    version: int


def _atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON data to a file."""
    import json

    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


class JsonStateStore[T: _VersionedState]:
    """Generic JSON state store with file locking and hot-reload.

    This is a simplified version of telegram's state_store that doesn't require msgspec.
    """

    def __init__(
        self,
        path: Path,
        *,
        version: int,
        state_type: type[T],
        state_factory: Callable[[], T],
        log_prefix: str,
    ) -> None:
        self._path = path
        self._lock = anyio.Lock()
        self._loaded = False
        self._mtime_ns: int | None = None
        self._state_type = state_type
        self._state_factory = state_factory
        self._version = version
        self._log_prefix = log_prefix
        self._state = state_factory()

    def _stat_mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def _reload_locked_if_needed(self) -> None:
        current = self._stat_mtime_ns()
        if self._loaded and current == self._mtime_ns:
            return
        self._load_locked()

    def _load_locked(self) -> None:
        import json

        self._loaded = True
        self._mtime_ns = self._stat_mtime_ns()
        if self._mtime_ns is None:
            self._state = self._state_factory()
            return
        try:
            data = json.loads(self._path.read_text())
            # Validate version
            if data.get("version") != self._version:
                logger.warning(
                    f"{self._log_prefix}.version_mismatch",
                    path=str(self._path),
                    version=data.get("version"),
                    expected=self._version,
                )
                self._state = self._state_factory()
                return
            # Create state from dict
            self._state = self._state_type(**data)
        except Exception as exc:
            logger.warning(
                f"{self._log_prefix}.load_failed",
                path=str(self._path),
                error=str(exc),
                error_type=exc.__class__.__name__,
            )
            self._state = self._state_factory()

    def _save_locked(self) -> None:
        from dataclasses import asdict

        payload = asdict(self._state)
        _atomic_write_json(self._path, payload)
        self._mtime_ns = self._stat_mtime_ns()
