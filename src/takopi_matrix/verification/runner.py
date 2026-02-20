from __future__ import annotations

import asyncio
from contextlib import suppress
import sys
import time
from typing import Any

import nio
from nio.crypto.device import OlmDevice
from nio.crypto.sas import Sas
from nio.event_builders.direct_messages import ToDeviceMessage
from nio.events.to_device import UnknownToDeviceEvent

from takopi.api import get_logger

from .creds import _MatrixCreds
from .keys import _extract_olm_device, _keys_query
from .nio_types import (
    KeyVerificationAccept,
    KeyVerificationCancel,
    KeyVerificationEvent,
    KeyVerificationKey,
    KeyVerificationMac,
    KeyVerificationStart,
)
from .olm_patch import _init_crypto
from .send import _send_encrypted, _send_plain, _send_verif, _tx_id

logger = get_logger("takopi_matrix.verify_device")


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
    initiate_retries: int,
    initiate_retry_interval_seconds: int,
    broadcast_request: bool | None,
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
    txn_channels: dict[str, str] = {}

    def _mark_txn_channel(txn: str, event: Any) -> None:
        txn = str(txn)
        if getattr(event, "_takopi_from_olm", False):
            prev = txn_channels.get(txn)
            txn_channels[txn] = "encrypted"
            if debug_events and prev != "encrypted":
                print(f"[debug] txn={txn}: channel=encrypted", flush=True)
            return
        txn_channels.setdefault(txn, "plaintext")
        if debug_events and txn_channels.get(txn) == "plaintext":
            print(f"[debug] txn={txn}: channel=plaintext", flush=True)

    def _is_active_txn(txn: str) -> bool:
        return (
            txn in requested_txns
            or txn in pending_txns
            or txn in verified_txns
            or txn in sent_mac_txns
            or txn in seen_mac_txns
        )

    async def _send_verif_txn(
        txn: str,
        target: OlmDevice,
        inner_type: str,
        inner_content: dict[str, Any],
        *,
        debug_events: bool,
    ) -> None:
        sp = bool(send_plaintext)
        se = bool(send_encrypted)
        channel = txn_channels.get(str(txn))
        if channel == "encrypted" and se:
            sp = False
        elif channel == "plaintext" and sp:
            se = False
        if not sp and not se:
            if send_encrypted:
                se = True
            elif send_plaintext:
                sp = True
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
        with suppress(Exception):
            client.device_store.add(dev)

    async def _ensure_device_keys(owner: str, dev_id: str) -> OlmDevice | None:
        dev_id = str(dev_id)
        if dev_id in device_by_id:
            return device_by_id[dev_id]

        data = await asyncio.to_thread(
            _keys_query, creds.homeserver, creds.access_token, owner
        )
        dev_map = (data.get("device_keys", {}) or {}).get(owner, {}) or {}
        info = dev_map.get(dev_id) if isinstance(dev_map, dict) else None
        if not isinstance(info, dict):
            return None

        dev = _extract_olm_device(owner, dev_id, info)
        if dev is None:
            return None
        _remember_device(dev)
        return dev

    async def _initiate_once(*, attempt: int) -> None:
        nonlocal expected_target_device_ids

        if not initiate_to:
            return

        try:
            data = await asyncio.to_thread(
                _keys_query, creds.homeserver, creds.access_token, initiate_to
            )
        except Exception as exc:
            print(
                f"[verifier] keys/query failed for {initiate_to}: {exc!r}", flush=True
            )
            return

        dev_map = (data.get("device_keys", {}) or {}).get(initiate_to, {}) or {}
        if not isinstance(dev_map, dict) or not dev_map:
            print(
                f"[verifier] no devices found for {initiate_to} via /keys/query",
                flush=True,
            )
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

        use_broadcast = broadcast_request
        if use_broadcast is None:
            use_broadcast = not initiate_device_ids
        should_broadcast = bool(use_broadcast) and attempt == 1

        if should_broadcast:
            try:
                txn = _tx_id()
                content = {
                    "from_device": creds.device_id,
                    "methods": ["m.sas.v1"],
                    "timestamp": int(time.time() * 1000),
                    "transaction_id": txn,
                }
                msg = ToDeviceMessage(
                    "m.key.verification.request", initiate_to, "*", content
                )
                await _send_plain(client, msg, debug_events=debug_events)
                requested_txns[txn] = "*"
                print(
                    f"[verifier] sent broadcast request to {initiate_to} txn={txn}",
                    flush=True,
                )
            except Exception as exc:
                print(f"[verifier] broadcast request error: {exc!r}", flush=True)
        elif debug_events and bool(use_broadcast):
            print(
                f"[debug] initiate attempt={attempt}: broadcast disabled after first attempt",
                flush=True,
            )

        sent_count = 0
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
            sent_count += 1
            label = f"{dev_id}"
            display_name = getattr(dev, "display_name", "") or ""
            if display_name:
                label = f"{dev_id} ({display_name})"
            print(
                f"[verifier] sent request to {initiate_to} device {label} txn={txn} attempt={attempt}",
                flush=True,
            )

        if debug_events:
            print(
                f"[debug] initiate attempt={attempt}: requested_devices={sent_count} retries={max(1, initiate_retries)} interval={max(0, initiate_retry_interval_seconds)}s",
                flush=True,
            )

        if verify_all and device_by_id:
            expected_target_device_ids.update(device_by_id.keys())
            print(
                f"[verifier] expecting to verify {len(expected_target_device_ids)} device(s) for {initiate_to}",
                flush=True,
            )

    async def _initiate_with_retries() -> None:
        attempts = max(1, int(initiate_retries))
        interval_seconds = max(0, int(initiate_retry_interval_seconds))

        for attempt in range(1, attempts + 1):
            if done.is_set():
                break
            await _initiate_once(attempt=attempt)
            if done.is_set() or attempt >= attempts:
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), timeout=float(interval_seconds))

        if debug_events and not done.is_set():
            print(
                f"[debug] initiate retries exhausted attempts={attempts}",
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

        event_type = getattr(event, "type", "") or ""
        if isinstance(event, UnknownToDeviceEvent):
            src = getattr(event, "source", {}) or {}
            content = src.get("content", {}) if isinstance(src, dict) else {}
        else:
            content = getattr(event, "content", {}) or {}
        if not isinstance(content, dict):
            content = {}

        if debug_events and event_type:
            txn_dbg = content.get("transaction_id") or getattr(
                event, "transaction_id", None
            )
            from_olm = bool(getattr(event, "_takopi_from_olm", False))
            print(
                f"[debug] event type={event_type} sender={sender} txn={txn_dbg} class={event.__class__.__name__} olm={from_olm}",
                flush=True,
            )
            if event_type in ("m.key.verification.request", "m.key.verification.ready"):
                from_device = content.get("from_device")
                methods = content.get("methods", content.get("method"))
                keys = sorted([k for k in content if isinstance(k, str)])
                print(
                    f"[debug] {event_type} from_device={from_device} methods={methods!r} keys={keys}",
                    flush=True,
                )

        txn_from_payload = content.get("transaction_id")
        if txn_from_payload is not None:
            txn_from_payload = str(txn_from_payload)
        stale_types = {
            "m.key.verification.ready",
            "m.key.verification.accept",
            "m.key.verification.key",
            "m.key.verification.mac",
            "m.key.verification.cancel",
            "m.key.verification.done",
        }
        if (
            debug_events
            and txn_from_payload
            and event_type in stale_types
            and not _is_active_txn(txn_from_payload)
        ):
            known = sorted(requested_txns.keys())[-5:]
            print(
                f"[debug] stale verification event ignored type={event_type} txn={txn_from_payload} known_recent_txns={known}",
                flush=True,
            )

        if isinstance(event, (UnknownToDeviceEvent, KeyVerificationEvent)):
            if event_type == "m.key.verification.request":
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

                if initiate_to:
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

                if send_plaintext:
                    await _send_plain(
                        client,
                        ToDeviceMessage(
                            "m.key.verification.ready", target.user_id, target.id, ready
                        ),
                        debug_events=debug_events,
                    )
                elif send_encrypted:
                    await _send_encrypted(
                        client,
                        target,
                        "m.key.verification.ready",
                        ready,
                        debug_events=debug_events,
                    )
                else:
                    print(
                        f"[verifier] request txn={req_txn}: cannot send ready (all send modes disabled)",
                        flush=True,
                    )
                    return
                print(
                    f"[verifier] request from {sender} txn={req_txn}: sent ready",
                    flush=True,
                )
                return

            if event_type == "m.key.verification.ready":
                ready_txn = content.get("transaction_id")
                from_device = content.get("from_device")
                if not ready_txn or not from_device:
                    if debug_events:
                        print(
                            f"[debug] ready missing txn/from_device content={content!r}",
                            flush=True,
                        )
                    return
                _mark_txn_channel(str(ready_txn), event)

                if (
                    initiate_to
                    and sender == initiate_to
                    and str(ready_txn) in requested_txns
                ):
                    target_dev_id = requested_txns.get(str(ready_txn)) or ""
                    if (
                        target_dev_id
                        and target_dev_id != "*"
                        and str(from_device) != target_dev_id
                    ):
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

                    olm = getattr(client, "olm", None)
                    if olm is None:
                        print(
                            "[verifier] cannot start SAS: olm not initialized",
                            flush=True,
                        )
                        return

                    fingerprint = olm.account.identity_keys["ed25519"]
                    sas = Sas(
                        creds.user_id,
                        creds.device_id,
                        fingerprint,
                        target,
                        transaction_id=str(ready_txn),
                    )
                    olm.key_verifications[str(ready_txn)] = sas
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

        txn = getattr(event, "transaction_id", None) or content.get("transaction_id")
        if not txn:
            return
        txn = str(txn)
        _mark_txn_channel(txn, event)

        if isinstance(event, KeyVerificationAccept):
            print(f"[verifier] accept from {sender} txn={txn}", flush=True)
            sas = _sas_for(txn)
            if sas is not None:
                if txn not in seen_accept_txns:
                    seen_accept_txns.add(txn)
                    try:
                        olm = getattr(client, "olm", None)
                        if olm is not None:
                            olm.handle_key_verification(event)
                    except Exception as exc:
                        if debug_events:
                            print(
                                f"[debug] handle_key_verification(accept) failed txn={txn}: {exc!r}",
                                flush=True,
                            )
                if txn in sent_key_txns:
                    if debug_events:
                        print(
                            f"[debug] accept txn={txn}: key already sent; ignoring",
                            flush=True,
                        )
                    return
                try:
                    key_msg = sas.share_key()
                except Exception as exc:
                    print(
                        f"[verifier] accept txn={txn}: share_key failed: {exc!r}",
                        flush=True,
                    )
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
            if debug_events:
                print(
                    f"[debug] start txn={txn}: from_olm={bool(getattr(event, '_takopi_from_olm', False))}",
                    flush=True,
                )
            if txn in seen_start_txns:
                if debug_events:
                    print(
                        f"[debug] start txn={txn}: already handled; ignoring duplicate",
                        flush=True,
                    )
                return
            seen_start_txns.add(txn)

            methods = getattr(event, "short_authentication_string", []) or []
            if "emoji" not in methods and "decimal" not in methods:
                print(
                    f"[verifier] start txn={txn}: unsupported SAS methods: {methods}",
                    flush=True,
                )
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
                olm = getattr(client, "olm", None)
                if olm is None:
                    print(
                        "[verifier] cannot accept SAS: olm not initialized", flush=True
                    )
                    return
                fingerprint = olm.account.identity_keys["ed25519"]
                sas = Sas.from_key_verification_start(
                    creds.user_id,
                    creds.device_id,
                    fingerprint,
                    target,
                    event,
                )
                olm.key_verifications[str(txn)] = sas

            other = getattr(sas, "other_olm_device", None) or target

            print(
                f"[verifier] start from {sender} {from_device} txn={txn} (sending accept)",
                flush=True,
            )
            pending_txns.add(txn)

            try:
                accept_msg = sas.accept_verification()
            except Exception as exc:
                print(
                    f"[verifier] start txn={txn}: accept build failed: {exc!r}",
                    flush=True,
                )
                return
            await _send_verif_txn(
                txn,
                other,
                accept_msg.type,
                accept_msg.content,
                debug_events=debug_events,
            )
            print(f"[verifier] start txn={txn}: sent accept", flush=True)

            pending_share_key.add(txn)
            return

        if isinstance(event, KeyVerificationKey):
            if debug_events:
                print(
                    f"[debug] key txn={txn}: from_olm={bool(getattr(event, '_takopi_from_olm', False))}",
                    flush=True,
                )
            sas = _sas_for(txn)
            if sas is None:
                print(f"[verifier] key txn={txn}: missing SAS state", flush=True)
                return

            if not getattr(sas, "other_key_set", False):
                try:
                    olm = getattr(client, "olm", None)
                    if olm is not None:
                        olm.handle_key_verification(event)
                except Exception as exc:
                    if debug_events:
                        print(
                            f"[debug] handle_key_verification(key) failed txn={txn}: {exc!r}",
                            flush=True,
                        )

            if txn in pending_share_key:
                pending_share_key.discard(txn)
                try:
                    key_msg = sas.share_key()
                except Exception as exc:
                    print(
                        f"[verifier] key txn={txn}: share_key failed: {exc!r}",
                        flush=True,
                    )
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
                with suppress(Exception):
                    print(
                        "[verifier] emojis:",
                        [(e.emoji, e.description) for e in sas.get_emoji()],
                        flush=True,
                    )
            if hasattr(sas, "get_decimals"):
                with suppress(Exception):
                    print("[verifier] decimals:", sas.get_decimals(), flush=True)

            print(f"[verifier] key txn={txn}: waiting for partner mac", flush=True)
            return

        if isinstance(event, KeyVerificationMac):
            if debug_events:
                print(
                    f"[debug] mac txn={txn}: from_olm={bool(getattr(event, '_takopi_from_olm', False))}",
                    flush=True,
                )
            sas = _sas_for(txn)
            if sas is None:
                print(f"[verifier] mac txn={txn}: missing SAS state", flush=True)
                done.set()
                return

            if txn not in seen_mac_txns:
                seen_mac_txns.add(txn)
                try:
                    olm = getattr(client, "olm", None)
                    if olm is not None:
                        olm.handle_key_verification(event)
                except Exception as exc:
                    if debug_events:
                        print(
                            f"[debug] handle_key_verification(mac) failed txn={txn}: {exc!r}",
                            flush=True,
                        )

            if not getattr(sas, "other_key_set", False):
                print(
                    f"[verifier] mac txn={txn}: other_key_set is false; cannot confirm yet",
                    flush=True,
                )
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
                        print(
                            f"[verifier] mac txn={txn}: confirm failed: {exc!r}",
                            flush=True,
                        )
                else:
                    print(
                        f"[verifier] mac txn={txn}: manual-confirm mode (not sending mac)",
                        flush=True,
                    )

            asyncio.create_task(_finish_when_verified(txn))

            if getattr(sas, "verified", False):
                sas_verified_devices = getattr(sas, "verified_devices", None)
                if sas_verified_devices is not None:
                    print(
                        "[verifier] verified_devices:", sas_verified_devices, flush=True
                    )
                else:
                    print("[verifier] verified (check UI)", flush=True)

                other_id = getattr(getattr(sas, "other_olm_device", None), "id", None)
                if other_id:
                    verified_device_ids.add(str(other_id))

                pending_txns.discard(txn)
                verified_txns.add(txn)

                if verify_all:
                    if (
                        expected_target_device_ids
                        and expected_target_device_ids.issubset(verified_device_ids)
                    ):
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
            seen_start_txns.discard(txn)
            txn_channels.pop(txn, None)
            with suppress(Exception):
                client.key_verifications.pop(txn, None)
            try:
                olm = getattr(client, "olm", None)
                if olm is not None:
                    olm.key_verifications.pop(txn, None)
            except Exception:
                pass

            if (
                verify_all
                and expected_target_device_ids
                and expected_target_device_ids.issubset(verified_device_ids)
            ):
                done.set()
            return

    def _on_to_device_callback(event: Any) -> None:
        asyncio.create_task(on_to_device(event))

    client.add_to_device_callback(
        _on_to_device_callback,
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
        sync_task = asyncio.create_task(
            client.sync_forever(timeout=30000, full_state=True)
        )
        initiate_task: asyncio.Task[None] | None = None
        if initiate_to:
            initiate_task = asyncio.create_task(_initiate_with_retries())

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
            if initiate_task is not None:
                initiate_task.cancel()
            sync_task.cancel()
            await client.close()

        if verified_device_ids:
            print(
                f"[verifier] verified devices: {sorted(verified_device_ids)}",
                flush=True,
            )
        if pending_txns:
            print(
                f"[verifier] pending txns not completed: {sorted(pending_txns)}",
                flush=True,
            )
        return 0
    except Exception as exc:
        logger.error(
            "matrix.verify_device.failed",
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        with suppress(Exception):
            await client.close()
        return 1
