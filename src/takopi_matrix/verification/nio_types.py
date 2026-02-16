"""Compatibility imports for matrix-nio verification event classes."""

from __future__ import annotations

try:
    from nio import (
        KeyVerificationAccept,
        KeyVerificationCancel,
        KeyVerificationEvent,
        KeyVerificationKey,
        KeyVerificationMac,
        KeyVerificationStart,
    )
except Exception:  # pragma: no cover
    # Older matrix-nio versions re-export these from nio.events.to_device
    from nio.events.to_device import (  # type: ignore[import-not-found]
        KeyVerificationAccept,
        KeyVerificationCancel,
        KeyVerificationEvent,
        KeyVerificationKey,
        KeyVerificationMac,
        KeyVerificationStart,
    )

__all__ = [
    "KeyVerificationAccept",
    "KeyVerificationCancel",
    "KeyVerificationEvent",
    "KeyVerificationKey",
    "KeyVerificationMac",
    "KeyVerificationStart",
]
