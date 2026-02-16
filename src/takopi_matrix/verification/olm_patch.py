from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

import nio
from nio.events.to_device import UnknownToDeviceEvent

from .creds import _MatrixCreds
from .keys import _keys_query
from .nio_types import (
    KeyVerificationAccept,
    KeyVerificationCancel,
    KeyVerificationEvent,
    KeyVerificationKey,
    KeyVerificationMac,
    KeyVerificationStart,
)


def _mark_from_olm(event: Any) -> Any:
    with suppress(Exception):
        event._takopi_from_olm = True
    return event


def _patch_olm_for_verification(
    client: nio.AsyncClient,
    *,
    debug_events: bool,
) -> None:
    """
    matrix-nio can decrypt Olm-wrapped to-device events (m.room.encrypted), but
    some clients embed m.key.verification.* payloads inside those wrappers.

    The verifier needs those decrypted payloads to:
    - reach callbacks as KeyVerification* events
    - update SAS state via olm.handle_key_verification() before acting
    """

    olm = getattr(client, "olm", None)
    if olm is None:
        return

    orig_handle_olm = getattr(olm, "_handle_olm_event", None)
    if callable(orig_handle_olm) and not getattr(olm, "_takopi_verif_patch", False):

        def _verif_content(payload: dict[str, Any]) -> dict[str, Any]:
            raw = payload.get("content")
            if isinstance(raw, dict):
                out = dict(raw)
                for key in (
                    "transaction_id",
                    "from_device",
                    "methods",
                    "method",
                    "timestamp",
                ):
                    if key in payload and key not in out:
                        out[key] = payload[key]
                return out

            out: dict[str, Any] = {}
            for key, value in payload.items():
                if key in (
                    "type",
                    "content",
                    "sender",
                    "recipient",
                    "sender_key",
                    "recipient_keys",
                    "keys",
                ):
                    continue
                out[key] = value
            return out

        def _patched_handle_olm(sender: str, sender_key: str, payload: dict[str, Any]):  # type: ignore[no-untyped-def]
            event_type = payload.get("type")
            if isinstance(event_type, str) and event_type.startswith(
                "m.key.verification."
            ):
                event_dict = {
                    "sender": sender,
                    "type": event_type,
                    "content": _verif_content(payload),
                }
                try:
                    if event_type == "m.key.verification.start":
                        return _mark_from_olm(
                            KeyVerificationStart.from_dict(event_dict)
                        )
                    if event_type == "m.key.verification.accept":
                        return _mark_from_olm(
                            KeyVerificationAccept.from_dict(event_dict)
                        )
                    if event_type == "m.key.verification.key":
                        return _mark_from_olm(KeyVerificationKey.from_dict(event_dict))
                    if event_type == "m.key.verification.mac":
                        return _mark_from_olm(KeyVerificationMac.from_dict(event_dict))
                    if event_type == "m.key.verification.cancel":
                        return _mark_from_olm(
                            KeyVerificationCancel.from_dict(event_dict)
                        )
                    return _mark_from_olm(UnknownToDeviceEvent.from_dict(event_dict))
                except Exception as exc:
                    if debug_events:
                        print(
                            f"[debug] failed to parse olm payload type={event_type}: {exc!r}",
                            flush=True,
                        )
                    try:
                        return _mark_from_olm(
                            UnknownToDeviceEvent.from_dict(event_dict)
                        )
                    except Exception:
                        return None
            return orig_handle_olm(sender, sender_key, payload)

        olm._handle_olm_event = _patched_handle_olm  # type: ignore[attr-defined]
        olm._takopi_verif_patch = True  # type: ignore[attr-defined]
        if debug_events:
            print(
                "[debug] patched olm for embedded key verification events", flush=True
            )

    orig_handle_to_device = getattr(olm, "handle_to_device_event", None)
    if callable(orig_handle_to_device) and not getattr(
        olm, "_takopi_decrypted_verif_patch", False
    ):

        def _patched_handle_to_device(event: Any):  # type: ignore[no-untyped-def]
            in_type = getattr(event, "type", "") or ""
            decrypted = orig_handle_to_device(event)
            if (
                in_type == "m.room.encrypted"
                and decrypted is not None
                and isinstance(decrypted, KeyVerificationEvent)
            ):
                try:
                    olm.handle_key_verification(decrypted)
                except Exception as exc:
                    if debug_events:
                        event_type = getattr(decrypted, "type", "") or ""
                        txn_id = getattr(decrypted, "transaction_id", None)
                        print(
                            f"[debug] handle_key_verification failed type={event_type} txn={txn_id}: {exc!r}",
                            flush=True,
                        )
            return decrypted

        olm.handle_to_device_event = _patched_handle_to_device  # type: ignore[assignment]
        olm._takopi_decrypted_verif_patch = True  # type: ignore[attr-defined]
        if debug_events:
            print(
                "[debug] patched olm.handle_to_device_event for decrypted verifications",
                flush=True,
            )


async def _init_crypto(
    client: nio.AsyncClient, *, creds: _MatrixCreds, debug_events: bool
) -> None:
    load_store = getattr(client, "load_store", None)
    if callable(load_store):
        load_store()

    _patch_olm_for_verification(client, debug_events=debug_events)

    try:
        await client.keys_upload()
    except Exception as exc:
        if debug_events:
            print(f"[debug] keys_upload skipped: {exc!r}", flush=True)

    data = await asyncio.to_thread(
        _keys_query, creds.homeserver, creds.access_token, creds.user_id
    )
    dev_map = (data.get("device_keys", {}) or {}).get(creds.user_id, {}) or {}
    info = dev_map.get(creds.device_id) if isinstance(dev_map, dict) else None
    keys = (info.get("keys") or {}) if isinstance(info, dict) else {}
    server_curve = keys.get(f"curve25519:{creds.device_id}")
    server_ed = keys.get(f"ed25519:{creds.device_id}")

    olm = getattr(client, "olm", None)
    local = olm.account.identity_keys if olm is not None else {}
    local_curve = local.get("curve25519")
    local_ed = local.get("ed25519")

    if server_curve and local_curve and server_curve != local_curve:
        raise RuntimeError(
            "Local curve25519 key does not match homeserver. "
            "Fix crypto_store_path (or mint a new device)."
        )
    if server_ed and local_ed and server_ed != local_ed:
        raise RuntimeError(
            "Local ed25519 key does not match homeserver. "
            "Fix crypto_store_path (or mint a new device)."
        )
