from __future__ import annotations

import uuid
from typing import Any

import nio
from nio.crypto.device import OlmDevice
from nio.event_builders.direct_messages import ToDeviceMessage


def _tx_id() -> str:
    return str(uuid.uuid4())


async def _send_plain(
    client: nio.AsyncClient, msg: ToDeviceMessage, *, debug_events: bool
) -> None:
    response = await client.to_device(msg, tx_id=_tx_id())
    if response.__class__.__name__.endswith("Error"):
        print(
            f"[verifier] send failed type={msg.type} to={msg.recipient}:{msg.recipient_device} ({response.__class__.__name__})",
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
            print(
                "[debug] olm._olm_encrypt not available; skipping encrypted send",
                flush=True,
            )
        return

    olm_dict = encrypt_fn(session, target, inner_type, inner_content)
    msg = ToDeviceMessage("m.room.encrypted", target.user_id, target.id, olm_dict)
    response = await client.to_device(msg, tx_id=_tx_id())
    if response.__class__.__name__.endswith("Error"):
        print(
            f"[verifier] encrypted send failed inner={inner_type} to={target.user_id}:{target.id} ({response.__class__.__name__})",
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
