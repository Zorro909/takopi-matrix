from __future__ import annotations

from pathlib import Path
from typing import Any


def _try_lock(lock_path: Path) -> Any:
    """
    Best-effort interprocess lock. Returns a file handle to keep open for the
    duration of the process, or None if locking isn't available.
    """

    try:
        import fcntl  # type: ignore
    except Exception:
        return None

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except Exception:
        lock_file.close()
        raise
