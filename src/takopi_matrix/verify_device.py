"""
SAS/emoji device verification helper for Matrix E2EE.

This is intended to be run as an explicit one-shot operator command, not as part
of the normal Takopi runtime.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import nio
from nio.crypto.device import OlmDevice
from nio.crypto.sas import Sas
from nio.event_builders.direct_messages import ToDeviceMessage
from nio.events.to_device import UnknownToDeviceEvent

from takopi.api import get_logger

logger = get_logger(__name__)

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


@dataclass(frozen=True)
class _MatrixCreds:
    homeserver: str
    user_id: str
    access_token: str
    device_id: str
    store_dir: Path


def _expand_path(s: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(s)))


def _load_takopi_toml(path: Path) -> dict[str, Any]:
    import tomllib

    raw = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("takopi.toml must parse to a table/dict")
    return data


def _cfg_get(d: dict[str, Any], *keys: str) -> Any:
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            raise KeyError("Missing config key: " + ".".join(keys))
        cur = cur[k]
    return cur


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _whoami(homeserver: str, token: str) -> dict[str, Any]:
    hs = homeserver.rstrip("/")
    r = httpx.get(
        f"{hs}/_matrix/client/v3/account/whoami",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    if r.status_code == 401:
        raise RuntimeError("whoami unauthorized (401)")
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("whoami returned non-object JSON")
    return data


def _resolve_creds(config_path: Path) -> _MatrixCreds:
    cfg = _load_takopi_toml(config_path)
    m = _cfg_get(cfg, "transports", "matrix")
    if not isinstance(m, dict):
        raise TypeError("transports.matrix must be a table/dict")

    homeserver = str(m.get("homeserver") or "").strip().rstrip("/")
    user_id = str(m.get("user_id") or "").strip()
    cfg_access_token = str(m.get("access_token") or "").strip()
    cfg_device_id = str(m.get("device_id") or "").strip()

    if not homeserver:
        raise ValueError("Missing transports.matrix.homeserver")
    if not user_id:
        raise ValueError("Missing transports.matrix.user_id")
    if not cfg_access_token and not _env("matrix_access_token") and not _env("MATRIX_ACCESS_TOKEN"):
        raise ValueError("Missing transports.matrix.access_token (or env matrix_access_token)")

    env_access_token = _env("matrix_access_token") or _env("MATRIX_ACCESS_TOKEN")
    env_device_id = _env("matrix_device_id") or _env("MATRIX_DEVICE_ID")

    # Prefer env vars when present (container-friendly), but validate; if they are
    # stale, fall back to config.
    access_token = env_access_token or cfg_access_token
    token_source = "env" if env_access_token else "config"

    try:
        who = _whoami(homeserver, access_token)
    except Exception as exc:
        if token_source == "env" and cfg_access_token:
            logger.warning(
                "matrix.verify_device.env_token_failed_fallback",
                error=str(exc),
            )
            access_token = cfg_access_token
            who = _whoami(homeserver, access_token)
            token_source = "config"
        else:
            raise

    who_user = str(who.get("user_id") or "")
    who_device = str(who.get("device_id") or "")

    if who_user and who_user != user_id:
        raise RuntimeError(
            "whoami mismatch: access token belongs to "
            f"{who_user!r} but config says {user_id!r}"
        )

    device_id = env_device_id or cfg_device_id or who_device
    if not device_id:
        raise ValueError("Missing transports.matrix.device_id and whoami returned none")

    if who_device and device_id != who_device:
        # Prefer a matching config device_id when env is stale; otherwise warn and
        # use whoami's device id.
        if env_device_id and cfg_device_id and cfg_device_id == who_device:
            logger.warning("matrix.verify_device.env_device_id_mismatch_fallback")
            device_id = cfg_device_id
        else:
            logger.warning(
                "matrix.verify_device.device_id_mismatch",
                configured=device_id,
                whoami=who_device,
            )
            device_id = who_device or device_id

    # Derive the matrix-nio store directory the same way the transport does.
    store_dir = Path.home() / ".takopi"
    raw_crypto_store = m.get("crypto_store_path")
    if isinstance(raw_crypto_store, str) and raw_crypto_store.strip():
        store_dir = _expand_path(raw_crypto_store).parent

    store_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "matrix.verify_device.creds_resolved",
        user_id=user_id,
        device_id=device_id,
        token_source=token_source,
        store_dir=str(store_dir),
    )
    return _MatrixCreds(
        homeserver=homeserver,
        user_id=user_id,
        access_token=access_token,
        device_id=device_id,
        store_dir=store_dir,
    )


def _keys_query(homeserver: str, token: str, user_id: str) -> dict[str, Any]:
    hs = homeserver.rstrip("/")
    r = httpx.post(
        f"{hs}/_matrix/client/v3/keys/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_keys": {user_id: []}},
        timeout=20.0,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("keys/query returned non-object JSON")
    return data


def _extract_olm_device(owner: str, dev_id: str, info: dict[str, Any]) -> OlmDevice | None:
    keys = info.get("keys") or {}
    if not isinstance(keys, dict):
        return None

    norm_keys: dict[str, str] = {}
    for k, v in keys.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.startswith("curve25519:"):
            norm_keys["curve25519"] = v
        elif k.startswith("ed25519:"):
            norm_keys["ed25519"] = v

    display_name = ""
    unsigned = info.get("unsigned") or {}
    if isinstance(unsigned, dict):
        display_name = str(unsigned.get("device_display_name") or "")

    if "curve25519" not in norm_keys or "ed25519" not in norm_keys:
        return None
    try:
        return OlmDevice(owner, dev_id, norm_keys, display_name=display_name)
    except Exception:
        return None


def _patch_olm_for_verification(
    client: nio.AsyncClient,
    *,
    debug_events: bool,
) -> None:
    """
    matrix-nio can decrypt Olm-wrapped to-device events (m.room.encrypted), but
    some clients embed m.key.verification.* payloads inside those wrappers.

    The verifier needs those decrypted payloads to:
    - reach our callbacks as KeyVerification* events
    - update SAS state via olm.handle_key_verification() before we act
    """

    olm = getattr(client, "olm", None)
    if olm is None:
        return

    # 1) Surface decrypted m.key.verification.* as ToDeviceEvent objects.
    orig_handle_olm = getattr(olm, "_handle_olm_event", None)
    if callable(orig_handle_olm) and not getattr(olm, "_takopi_verif_patch", False):

        def _verif_content(payload: dict[str, Any]) -> dict[str, Any]:
            """
            Some clients encrypt verification events in a non-standard shape.

            Expected (spec-ish): {"type": ..., "content": {...}}
            Observed: {"type": ..., "transaction_id": ..., "from_device": ...}

            Normalize to a dict that behaves like the to-device event content.
            """

            raw = payload.get("content")
            if isinstance(raw, dict):
                out = dict(raw)
                # If the content is empty/missing fields, try top-level fallbacks.
                for k in (
                    "transaction_id",
                    "from_device",
                    "methods",
                    "method",
                    "timestamp",
                ):
                    if k in payload and k not in out:
                        out[k] = payload[k]
                return out

            out: dict[str, Any] = {}
            for k, v in payload.items():
                if k in (
                    "type",
                    "content",
                    "sender",
                    "recipient",
                    "sender_key",
                    "recipient_keys",
                    "keys",
                ):
                    continue
                out[k] = v
            return out

        def _patched_handle_olm(sender: str, sender_key: str, payload: dict[str, Any]):  # type: ignore[no-untyped-def]
            t = payload.get("type")
            if isinstance(t, str) and t.startswith("m.key.verification."):
                event_dict = {
                    "sender": sender,
                    "type": t,
                    "content": _verif_content(payload),
                }
                try:
                    if t == "m.key.verification.start":
                        evt = KeyVerificationStart.from_dict(event_dict)
                        try:
                            setattr(evt, "_takopi_from_olm", True)
                        except Exception:
                            pass
                        return evt
                    if t == "m.key.verification.accept":
                        evt = KeyVerificationAccept.from_dict(event_dict)
                        try:
                            setattr(evt, "_takopi_from_olm", True)
                        except Exception:
                            pass
                        return evt
                    if t == "m.key.verification.key":
                        evt = KeyVerificationKey.from_dict(event_dict)
                        try:
                            setattr(evt, "_takopi_from_olm", True)
                        except Exception:
                            pass
                        return evt
                    if t == "m.key.verification.mac":
                        evt = KeyVerificationMac.from_dict(event_dict)
                        try:
                            setattr(evt, "_takopi_from_olm", True)
                        except Exception:
                            pass
                        return evt
                    if t == "m.key.verification.cancel":
                        evt = KeyVerificationCancel.from_dict(event_dict)
                        try:
                            setattr(evt, "_takopi_from_olm", True)
                        except Exception:
                            pass
                        return evt
                    evt = UnknownToDeviceEvent.from_dict(event_dict)
                    try:
                        setattr(evt, "_takopi_from_olm", True)
                    except Exception:
                        pass
                    return evt
                except Exception as exc:
                    if debug_events:
                        print(
                            f"[debug] failed to parse olm payload type={t}: {exc!r}",
                            flush=True,
                        )
                    try:
                        evt = UnknownToDeviceEvent.from_dict(event_dict)
                        try:
                            setattr(evt, "_takopi_from_olm", True)
                        except Exception:
                            pass
                        return evt
                    except Exception:
                        return None
            return orig_handle_olm(sender, sender_key, payload)

        olm._handle_olm_event = _patched_handle_olm  # type: ignore[attr-defined]
        olm._takopi_verif_patch = True  # type: ignore[attr-defined]
        if debug_events:
            print("[debug] patched olm for embedded key verification events", flush=True)

    # 2) Ensure decrypted key verification events update SAS state.
    orig_handle_to_device = getattr(olm, "handle_to_device_event", None)
    if callable(orig_handle_to_device) and not getattr(olm, "_takopi_decrypted_verif_patch", False):

        def _patched_handle_to_device(event: Any):  # type: ignore[no-untyped-def]
            in_type = getattr(event, "type", "") or ""
            decrypted = orig_handle_to_device(event)
            # Only pre-handle *decrypted* verification events. Plaintext events
            # are already handled by matrix-nio; double-processing can trigger
            # m.unexpected_message cancellations.
            if (
                in_type == "m.room.encrypted"
                and decrypted is not None
                and isinstance(decrypted, KeyVerificationEvent)
            ):
                try:
                    olm.handle_key_verification(decrypted)
                except Exception as exc:
                    if debug_events:
                        et = getattr(decrypted, "type", "") or ""
                        tx = getattr(decrypted, "transaction_id", None)
                        print(
                            f"[debug] handle_key_verification failed type={et} txn={tx}: {exc!r}",
                            flush=True,
                        )
            return decrypted

        olm.handle_to_device_event = _patched_handle_to_device  # type: ignore[assignment]
        olm._takopi_decrypted_verif_patch = True  # type: ignore[attr-defined]
        if debug_events:
            print("[debug] patched olm.handle_to_device_event for decrypted verifications", flush=True)


async def _init_crypto(client: nio.AsyncClient, *, creds: _MatrixCreds, debug_events: bool) -> None:
    load_store = getattr(client, "load_store", None)
    if callable(load_store):
        load_store()

    _patch_olm_for_verification(client, debug_events=debug_events)

    # Ensure one-time keys exist; some clients require an Olm session for
    # verification events.
    try:
        await client.keys_upload()
    except Exception as exc:
        if debug_events:
            print(f"[debug] keys_upload skipped: {exc!r}", flush=True)

    # Sanity-check that our local crypto identity matches the homeserver for
    # this (user_id, device_id). If it doesn't, verification cannot work.
    data = await asyncio.to_thread(_keys_query, creds.homeserver, creds.access_token, creds.user_id)
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


def _tx_id() -> str:
    return str(uuid.uuid4())


async def _send_plain(client: nio.AsyncClient, msg: ToDeviceMessage, *, debug_events: bool) -> None:
    resp = await client.to_device(msg, tx_id=_tx_id())
    if resp.__class__.__name__.endswith("Error"):
        print(
            f"[verifier] send failed type={msg.type} to={msg.recipient}:{msg.recipient_device} ({resp.__class__.__name__})",
            flush=True,
        )
    elif debug_events:
        print(
            f"[debug] sent plaintext type={msg.type} to={msg.recipient}:{msg.recipient_device}",
            flush=True,
        )


async def _send_encrypted(
    client: nio.AsyncClient,
    target: OlmDevice,
    inner_type: str,
    inner_content: dict[str, Any],
    *,
    debug_events: bool,
) -> None:
    olm = getattr(client, "olm", None)
    if olm is None:
        if debug_events:
            print("[debug] cannot send encrypted: olm not initialized", flush=True)
        return

    session = olm.session_store.get(target.curve25519)
    if not session:
        try:
            await client.keys_claim({target.user_id: [target.id]})
        except Exception as exc:
            if debug_events:
                print(
                    f"[debug] keys_claim failed for {target.user_id} {target.id}: {exc!r}",
                    flush=True,
                )
        session = olm.session_store.get(target.curve25519)

    if not session:
        if debug_events:
            print(
                f"[debug] missing Olm session for {target.user_id} {target.id}; cannot encrypt",
                flush=True,
            )
        return

    encrypt_fn = getattr(olm, "_olm_encrypt", None)
    if not callable(encrypt_fn):
        if debug_events:
            print("[debug] olm._olm_encrypt not available; skipping encrypted send", flush=True)
        return

    # Private API, but currently the only practical way to encrypt ad-hoc
    # to-device verification payloads.
    olm_dict = encrypt_fn(session, target, inner_type, inner_content)
    msg = ToDeviceMessage("m.room.encrypted", target.user_id, target.id, olm_dict)
    resp = await client.to_device(msg, tx_id=_tx_id())
    if resp.__class__.__name__.endswith("Error"):
        print(
            f"[verifier] encrypted send failed inner={inner_type} to={target.user_id}:{target.id} ({resp.__class__.__name__})",
            flush=True,
        )
    elif debug_events:
        print(
            f"[debug] sent encrypted inner={inner_type} to={target.user_id}:{target.id}",
            flush=True,
        )


async def _send_verif(
    client: nio.AsyncClient,
    target: OlmDevice,
    inner_type: str,
    inner_content: dict[str, Any],
    *,
    send_plaintext: bool,
    send_encrypted: bool,
    debug_events: bool,
) -> None:
    if send_plaintext:
        await _send_plain(
            client,
            ToDeviceMessage(inner_type, target.user_id, target.id, inner_content),
            debug_events=debug_events,
        )
    if send_encrypted:
        await _send_encrypted(
            client,
            target,
            inner_type,
            inner_content,
            debug_events=debug_events,
        )


async def _run_verifier(
    *,
    creds: _MatrixCreds,
    allowed_senders: set[str],
    auto_confirm: bool,
    max_wait_seconds: int,
    debug_events: bool,
    send_plaintext: bool,
    send_encrypted: bool,
    initiate_to: str,
    initiate_device_ids: set[str],
    verify_all: bool,
) -> int:
    if not allowed_senders:
        print(
            "[verifier] warning: no --allow provided; accepting verification from any sender",
            file=sys.stderr,
            flush=True,
        )

    client_cfg = nio.AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)
    client = nio.AsyncClient(
        creds.homeserver,
        creds.user_id,
        device_id=creds.device_id,
        store_path=str(creds.store_dir),
        config=client_cfg,
    )
    client.access_token = creds.access_token
    client.user_id = creds.user_id
    client.device_id = creds.device_id

    done = asyncio.Event()

    pending_txns: set[str] = set()
    verified_txns: set[str] = set()
    verified_device_ids: set[str] = set()
    pending_share_key: set[str] = set()
    seen_start_txns: set[str] = set()
    requested_txns: dict[str, str] = {}
    device_by_id: dict[str, OlmDevice] = {}
    expected_target_device_ids: set[str] = set()
    sent_mac_txns: set[str] = set()
    seen_mac_txns: set[str] = set()
    seen_accept_txns: set[str] = set()
    sent_key_txns: set[str] = set()
    encrypted_txns: set[str] = set()

    def _mark_txn_encrypted(txn: str, event: Any) -> None:
        if getattr(event, "_takopi_from_olm", False):
            encrypted_txns.add(str(txn))

    async def _send_verif_txn(
        txn: str,
        target: OlmDevice,
        inner_type: str,
        inner_content: dict[str, Any],
        *,
        debug_events: bool,
    ) -> None:
        # Once we see any verification event via Olm (m.room.encrypted wrapper),
        # prefer encrypted-only replies for that transaction to avoid some clients
        # cancelling due to duplicate plaintext+encrypted messages.
        sp = bool(send_plaintext)
        se = bool(send_encrypted)
        if str(txn) in encrypted_txns and se:
            sp = False
        await _send_verif(
            client,
            target,
            inner_type,
            inner_content,
            send_plaintext=sp,
            send_encrypted=se,
            debug_events=debug_events,
        )

    def _remember_device(dev: OlmDevice) -> None:
        device_by_id[str(dev.id)] = dev
        try:
            client.device_store.add(dev)
        except Exception:
            pass

    async def _ensure_device_keys(owner: str, dev_id: str) -> OlmDevice | None:
        dev_id = str(dev_id)
        if dev_id in device_by_id:
            return device_by_id[dev_id]

        data = await asyncio.to_thread(_keys_query, creds.homeserver, creds.access_token, owner)
        dev_map = (data.get("device_keys", {}) or {}).get(owner, {}) or {}
        info = dev_map.get(dev_id) if isinstance(dev_map, dict) else None
        if not isinstance(info, dict):
            return None

        dev = _extract_olm_device(owner, dev_id, info)
        if dev is None:
            return None
        _remember_device(dev)
        return dev

    async def _initiate() -> None:
        nonlocal expected_target_device_ids

        if not initiate_to:
            return

        try:
            data = await asyncio.to_thread(_keys_query, creds.homeserver, creds.access_token, initiate_to)
        except Exception as exc:
            print(f"[verifier] keys/query failed for {initiate_to}: {exc!r}", flush=True)
            return

        dev_map = (data.get("device_keys", {}) or {}).get(initiate_to, {}) or {}
        if not isinstance(dev_map, dict) or not dev_map:
            print(f"[verifier] no devices found for {initiate_to} via /keys/query", flush=True)
            return

        for dev_id, info in sorted(dev_map.items()):
            if not isinstance(info, dict):
                continue
            if initiate_device_ids and str(dev_id) not in initiate_device_ids:
                continue
            if initiate_to == creds.user_id and str(dev_id) == creds.device_id:
                continue
            dev = _extract_olm_device(initiate_to, str(dev_id), info)
            if dev is None:
                continue
            _remember_device(dev)

        # Broadcast request so at least one active client can surface a popup.
        if not initiate_device_ids:
            try:
                txn = _tx_id()
                content = {
                    "from_device": creds.device_id,
                    "methods": ["m.sas.v1"],
                    "timestamp": int(time.time() * 1000),
                    "transaction_id": txn,
                }
                msg = ToDeviceMessage("m.key.verification.request", initiate_to, "*", content)
                await _send_plain(client, msg, debug_events=debug_events)
                requested_txns[txn] = "*"
                print(f"[verifier] sent broadcast request to {initiate_to} txn={txn}", flush=True)
            except Exception as exc:
                print(f"[verifier] broadcast request error: {exc!r}", flush=True)

        # Request verification with every discovered device.
        for dev_id, dev in sorted(device_by_id.items()):
            if initiate_device_ids and str(dev_id) not in initiate_device_ids:
                continue
            txn = _tx_id()
            content = {
                "from_device": creds.device_id,
                "methods": ["m.sas.v1"],
                "timestamp": int(time.time() * 1000),
                "transaction_id": txn,
            }
            await _send_verif(
                client,
                dev,
                "m.key.verification.request",
                content,
                send_plaintext=send_plaintext,
                send_encrypted=send_encrypted,
                debug_events=debug_events,
            )
            requested_txns[txn] = str(dev_id)
            label = f"{dev_id}"
            dn = getattr(dev, "display_name", "") or ""
            if dn:
                label = f"{dev_id} ({dn})"
            print(f"[verifier] sent request to {initiate_to} device {label} txn={txn}", flush=True)

        if verify_all and device_by_id:
            expected_target_device_ids.update(device_by_id.keys())
            print(
                f"[verifier] expecting to verify {len(expected_target_device_ids)} device(s) for {initiate_to}",
                flush=True,
            )

    def _sas_for(txn: str) -> Any | None:
        key_vers = getattr(client, "key_verifications", None)
        if isinstance(key_vers, dict):
            return key_vers.get(txn)
        return None

    async def _finish_when_verified(txn: str) -> None:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            sas = _sas_for(txn)
            if sas is not None and getattr(sas, "verified", False):
                return
            await asyncio.sleep(0.05)

    async def on_to_device(event: Any) -> None:
        sender = getattr(event, "sender", None)
        if sender and allowed_senders and sender not in allowed_senders:
            return

        etype = getattr(event, "type", "") or ""
        if isinstance(event, UnknownToDeviceEvent):
            # matrix-nio stores UnknownToDeviceEvent content under .source, not
            # .content. (This is the common case for request/ready.)
            src = getattr(event, "source", {}) or {}
            content = src.get("content", {}) if isinstance(src, dict) else {}
        else:
            content = getattr(event, "content", {}) or {}
        if not isinstance(content, dict):
            content = {}

        if debug_events and etype:
            txn_dbg = content.get("transaction_id") or getattr(event, "transaction_id", None)
            from_olm = bool(getattr(event, "_takopi_from_olm", False))
            print(
                f"[debug] event type={etype} sender={sender} txn={txn_dbg} class={event.__class__.__name__} olm={from_olm}",
                flush=True,
            )
            if etype in ("m.key.verification.request", "m.key.verification.ready"):
                fd = content.get("from_device")
                meth = content.get("methods", content.get("method"))
                keys = sorted([k for k in content.keys() if isinstance(k, str)])
                print(
                    f"[debug] {etype} from_device={fd} methods={meth!r} keys={keys}",
                    flush=True,
                )

        # Request/ready arrive as UnknownToDeviceEvent for many clients.
        if isinstance(event, (UnknownToDeviceEvent, KeyVerificationEvent)):
            if etype == "m.key.verification.request":
                req_txn = content.get("transaction_id")
                from_device = content.get("from_device")
                methods = content.get("methods") or []
                if not req_txn or not from_device:
                    if debug_events:
                        print(
                            f"[debug] request missing txn/from_device content={content!r}",
                            flush=True,
                        )
                    return
                _mark_txn_encrypted(str(req_txn), event)

                if initiate_to:
                    # We're initiating; ask the operator to accept the incoming
                    # request on the client instead of responding here.
                    print(
                        f"[verifier] request from {sender} txn={req_txn}: ignoring (initiator mode)",
                        flush=True,
                    )
                    return

                chosen = [m for m in methods if m in ("m.sas.v1",)] or ["m.sas.v1"]
                ready = {
                    "from_device": creds.device_id,
                    "methods": chosen,
                    "transaction_id": str(req_txn),
                }
                target = await _ensure_device_keys(str(sender), str(from_device))
                if target is None:
                    print(
                        f"[verifier] request txn={req_txn}: missing device keys for {sender} {from_device}",
                        flush=True,
                    )
                    return

                # Some clients send request/ready wrapped in Olm but still only
                # reliably accept plaintext ready. For ready specifically, do
                # not force "encrypted-only" even if we saw an Olm wrapper; send
                # both when enabled.
                await _send_verif(
                    client,
                    target,
                    "m.key.verification.ready",
                    ready,
                    send_plaintext=send_plaintext,
                    send_encrypted=send_encrypted,
                    debug_events=debug_events,
                )
                print(f"[verifier] request from {sender} txn={req_txn}: sent ready", flush=True)
                return

            if etype == "m.key.verification.ready":
                ready_txn = content.get("transaction_id")
                from_device = content.get("from_device")
                if not ready_txn or not from_device:
                    if debug_events:
                        print(
                            f"[debug] ready missing txn/from_device content={content!r}",
                            flush=True,
                        )
                    return
                _mark_txn_encrypted(str(ready_txn), event)

                if initiate_to and sender == initiate_to and str(ready_txn) in requested_txns:
                    target_dev_id = requested_txns.get(str(ready_txn)) or ""
                    if target_dev_id and target_dev_id != "*" and str(from_device) != target_dev_id:
                        print(
                            f"[verifier] ready txn={ready_txn}: got from_device={from_device} expected={target_dev_id} (continuing)",
                            flush=True,
                        )

                    target = await _ensure_device_keys(str(sender), str(from_device))
                    if target is None:
                        print(
                            f"[verifier] ready txn={ready_txn}: missing device keys for {sender} {from_device}",
                            flush=True,
                        )
                        return

                    if getattr(client, "olm", None) is None:
                        print("[verifier] cannot start SAS: olm not initialized", flush=True)
                        return

                    fp = client.olm.account.identity_keys["ed25519"]
                    sas = Sas(creds.user_id, creds.device_id, fp, target, transaction_id=str(ready_txn))
                    client.olm.key_verifications[str(ready_txn)] = sas
                    start_msg = sas.start_verification()

                    await _send_verif_txn(
                        str(ready_txn),
                        target,
                        "m.key.verification.start",
                        start_msg.content,
                        debug_events=debug_events,
                    )

                    pending_txns.add(str(ready_txn))
                    print(
                        f"[verifier] ready from {sender} {from_device} txn={ready_txn}: sent start",
                        flush=True,
                    )
                return

        txn = getattr(event, "transaction_id", None)
        if not txn:
            return
        txn = str(txn)
        _mark_txn_encrypted(txn, event)

        if isinstance(event, KeyVerificationAccept):
            print(f"[verifier] accept from {sender} txn={txn}", flush=True)
            sas = _sas_for(txn)
            if sas is not None:
                if txn not in seen_accept_txns:
                    seen_accept_txns.add(txn)
                    try:
                        if getattr(client, "olm", None) is not None:
                            client.olm.handle_key_verification(event)
                    except Exception as exc:
                        if debug_events:
                            print(
                                f"[debug] handle_key_verification(accept) failed txn={txn}: {exc!r}",
                                flush=True,
                            )
                if txn in sent_key_txns:
                    if debug_events:
                        print(f"[debug] accept txn={txn}: key already sent; ignoring", flush=True)
                    return
                try:
                    key_msg = sas.share_key()
                except Exception as exc:
                    print(f"[verifier] accept txn={txn}: share_key failed: {exc!r}", flush=True)
                    return
                await _send_verif_txn(
                    txn,
                    sas.other_olm_device,
                    key_msg.type,
                    key_msg.content,
                    debug_events=debug_events,
                )
                sent_key_txns.add(txn)
                print(f"[verifier] accept txn={txn}: sent key", flush=True)
            return

        if isinstance(event, KeyVerificationStart):
            if txn in seen_start_txns:
                if debug_events:
                    print(f"[debug] start txn={txn}: already handled; ignoring duplicate", flush=True)
                return
            seen_start_txns.add(txn)

            methods = getattr(event, "short_authentication_string", []) or []
            if "emoji" not in methods and "decimal" not in methods:
                print(f"[verifier] start txn={txn}: unsupported SAS methods: {methods}", flush=True)
                return

            from_device = getattr(event, "from_device", None)
            if not from_device:
                print(f"[verifier] start txn={txn}: missing from_device", flush=True)
                return

            target = await _ensure_device_keys(str(sender), str(from_device))
            if target is None:
                print(
                    f"[verifier] start txn={txn}: missing device keys for {sender} {from_device}",
                    flush=True,
                )
                return

            sas = _sas_for(txn)
            if sas is None:
                if getattr(client, "olm", None) is None:
                    print("[verifier] cannot accept SAS: olm not initialized", flush=True)
                    return
                fp = client.olm.account.identity_keys["ed25519"]
                sas = Sas.from_key_verification_start(creds.user_id, creds.device_id, fp, target, event)
                client.olm.key_verifications[str(txn)] = sas

            other = getattr(sas, "other_olm_device", None) or target

            print(f"[verifier] start from {sender} {from_device} txn={txn} (sending accept)", flush=True)
            pending_txns.add(txn)

            try:
                accept_msg = sas.accept_verification()
            except Exception as exc:
                print(f"[verifier] start txn={txn}: accept build failed: {exc!r}", flush=True)
                return
            await _send_verif_txn(
                txn,
                other,
                accept_msg.type,
                accept_msg.content,
                debug_events=debug_events,
            )
            print(f"[verifier] start txn={txn}: sent accept", flush=True)

            # As responder, wait for partner key then reply with our key.
            pending_share_key.add(txn)
            return

        if isinstance(event, KeyVerificationKey):
            sas = _sas_for(txn)
            if sas is None:
                print(f"[verifier] key txn={txn}: missing SAS state", flush=True)
                return

            if not getattr(sas, "other_key_set", False):
                try:
                    if getattr(client, "olm", None) is not None:
                        client.olm.handle_key_verification(event)
                except Exception as exc:
                    if debug_events:
                        print(f"[debug] handle_key_verification(key) failed txn={txn}: {exc!r}", flush=True)

            if txn in pending_share_key:
                pending_share_key.discard(txn)
                try:
                    key_msg = sas.share_key()
                except Exception as exc:
                    print(f"[verifier] key txn={txn}: share_key failed: {exc!r}", flush=True)
                    return
                await _send_verif_txn(
                    txn,
                    sas.other_olm_device,
                    key_msg.type,
                    key_msg.content,
                    debug_events=debug_events,
                )
                print(f"[verifier] key txn={txn}: sent key", flush=True)

            if hasattr(sas, "get_emoji"):
                try:
                    print(
                        "[verifier] emojis:",
                        [(e.emoji, e.description) for e in sas.get_emoji()],
                        flush=True,
                    )
                except Exception:
                    pass
            if hasattr(sas, "get_decimals"):
                try:
                    print("[verifier] decimals:", sas.get_decimals(), flush=True)
                except Exception:
                    pass

            print(f"[verifier] key txn={txn}: waiting for partner mac", flush=True)
            return

        if isinstance(event, KeyVerificationMac):
            sas = _sas_for(txn)
            if sas is None:
                print(f"[verifier] mac txn={txn}: missing SAS state", flush=True)
                done.set()
                return

            if txn not in seen_mac_txns:
                seen_mac_txns.add(txn)
                try:
                    if getattr(client, "olm", None) is not None:
                        client.olm.handle_key_verification(event)
                except Exception as exc:
                    if debug_events:
                        print(f"[debug] handle_key_verification(mac) failed txn={txn}: {exc!r}", flush=True)

            if not getattr(sas, "other_key_set", False):
                print(f"[verifier] mac txn={txn}: other_key_set is false; cannot confirm yet", flush=True)
                return

            if txn not in sent_mac_txns:
                if auto_confirm:
                    try:
                        msg = client.confirm_key_verification(txn)
                        await _send_verif_txn(
                            txn,
                            sas.other_olm_device,
                            msg.type,
                            msg.content,
                            debug_events=debug_events,
                        )
                        sent_mac_txns.add(txn)
                        print(f"[verifier] mac txn={txn}: sent mac", flush=True)
                    except Exception as exc:
                        print(f"[verifier] mac txn={txn}: confirm failed: {exc!r}", flush=True)
                else:
                    print(f"[verifier] mac txn={txn}: manual-confirm mode (not sending mac)", flush=True)

            asyncio.create_task(_finish_when_verified(txn))

            if getattr(sas, "verified", False):
                sas_verified_devices = getattr(sas, "verified_devices", None)
                if sas_verified_devices is not None:
                    print("[verifier] verified_devices:", sas_verified_devices, flush=True)
                else:
                    print("[verifier] verified (check UI)", flush=True)

                other_id = getattr(getattr(sas, "other_olm_device", None), "id", None)
                if other_id:
                    verified_device_ids.add(str(other_id))

                pending_txns.discard(txn)
                verified_txns.add(txn)

                if verify_all:
                    if expected_target_device_ids and expected_target_device_ids.issubset(verified_device_ids):
                        done.set()
                else:
                    done.set()
            else:
                print(
                    f"[verifier] mac txn={txn}: received but not verified yet (state={getattr(sas, 'state', None)!r})",
                    flush=True,
                )
            return

        if isinstance(event, KeyVerificationCancel):
            reason = getattr(event, "reason", "unknown")
            code = getattr(event, "code", None)
            if code:
                print(f"[verifier] cancelled txn={txn}: {code} {reason}", flush=True)
            else:
                print(f"[verifier] cancelled txn={txn}: {reason}", flush=True)
            pending_txns.discard(txn)
            pending_share_key.discard(txn)
            try:
                client.key_verifications.pop(txn, None)
            except Exception:
                pass
            try:
                if getattr(client, "olm", None) is not None:
                    client.olm.key_verifications.pop(txn, None)
            except Exception:
                pass

            # Don't exit on cancel; keep running so the operator can retry
            # immediately without restarting the helper.
            if verify_all and expected_target_device_ids and expected_target_device_ids.issubset(verified_device_ids):
                done.set()
            return

    client.add_to_device_callback(
        on_to_device,
        (
            UnknownToDeviceEvent,
            KeyVerificationEvent,
            KeyVerificationStart,
            KeyVerificationAccept,
            KeyVerificationKey,
            KeyVerificationMac,
            KeyVerificationCancel,
        ),
    )

    try:
        await _init_crypto(client, creds=creds, debug_events=debug_events)
        await _initiate()
        sync_task = asyncio.create_task(client.sync_forever(timeout=30000, full_state=True))

        try:
            if max_wait_seconds > 0:
                await asyncio.wait_for(done.wait(), timeout=float(max_wait_seconds))
            else:
                await done.wait()
        except TimeoutError:
            print(
                f"[verifier] timed out waiting for verification events (max_wait={max_wait_seconds})",
                file=sys.stderr,
                flush=True,
            )
            return 2
        finally:
            sync_task.cancel()
            await client.close()

        if verified_device_ids:
            print(f"[verifier] verified devices: {sorted(verified_device_ids)}", flush=True)
        if pending_txns:
            print(f"[verifier] pending txns not completed: {sorted(pending_txns)}", flush=True)
        return 0
    except Exception as exc:
        logger.error(
            "matrix.verify_device.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        try:
            await client.close()
        except Exception:
            pass
        return 1


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
    f = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except Exception:
        f.close()
        raise


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
                initiate_device_ids={s.strip() for s in initiate_device_ids if s.strip()},
                verify_all=bool(verify_all),
            )
        )
    finally:
        try:
            if lock_fh is not None:
                lock_fh.close()
        except Exception:
            pass
