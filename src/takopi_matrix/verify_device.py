"""
SAS/emoji device verification helper for Matrix E2EE.

This is intended to be run as an explicit one-shot operator command, not as part
of the normal Takopi runtime.

The implementation details live under `takopi_matrix.verification.*`. This file
is kept intentionally small because it is the CLI surface and a stable import
target for tests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from .verification.creds import _expand_path, _resolve_creds
from .verification.keys import _extract_olm_device
from .verification.lock import _try_lock
from .verification.runner import _run_verifier

__all__ = ["run_verify_device", "_resolve_creds", "_extract_olm_device"]


def run_verify_device(
    *,
    config_path: str,
    allowed_senders: set[str],
    auto_confirm: bool,
    max_wait_seconds: int,
    debug_events: bool,
    send_plaintext: bool,
    send_encrypted: bool,
    initiate_to: str,
    initiate_device_ids: set[str],
    initiate_retries: int,
    initiate_retry_interval_seconds: int,
    broadcast_request: bool | None,
    verify_all: bool,
) -> int:
    cfg_path = _expand_path(config_path)
    if not cfg_path.exists() or not cfg_path.is_file():
        print(f"Missing config at: {cfg_path}", file=sys.stderr)
        return 2

    # Keep this aligned with typical container stacks which already use a flock
    # lock around the main takopi process.
    lock_path = Path.home() / ".takopi" / "takopi.flock.lock"
    lock_fh = None
    try:
        lock_fh = _try_lock(lock_path)
    except Exception:
        print(
            f"Lock is held ({lock_path}). Stop takopi and retry.",
            file=sys.stderr,
        )
        return 2

    try:
        creds = _resolve_creds(cfg_path)
    except Exception as exc:
        print(f"Failed to load Matrix config: {exc}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(
            _run_verifier(
                creds=creds,
                allowed_senders={s.strip() for s in allowed_senders if s.strip()},
                auto_confirm=bool(auto_confirm),
                max_wait_seconds=int(max_wait_seconds),
                debug_events=bool(debug_events),
                send_plaintext=bool(send_plaintext),
                send_encrypted=bool(send_encrypted),
                initiate_to=str(initiate_to).strip(),
                initiate_device_ids={
                    s.strip() for s in initiate_device_ids if s.strip()
                },
                initiate_retries=max(1, int(initiate_retries)),
                initiate_retry_interval_seconds=max(
                    0, int(initiate_retry_interval_seconds)
                ),
                broadcast_request=broadcast_request,
                verify_all=bool(verify_all),
            )
        )
    finally:
        try:
            if lock_fh is not None:
                lock_fh.close()
        except Exception:
            pass
