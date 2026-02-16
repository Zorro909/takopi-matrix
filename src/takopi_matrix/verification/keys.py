from __future__ import annotations

from typing import Any

import httpx
from nio.crypto.device import OlmDevice


def _keys_query(homeserver: str, token: str, user_id: str) -> dict[str, Any]:
    hs = homeserver.rstrip("/")
    response = httpx.post(
        f"{hs}/_matrix/client/v3/keys/query",
        headers={"Authorization": f"Bearer {token}"},
        json={"device_keys": {user_id: []}},
        timeout=20.0,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("keys/query returned non-object JSON")
    return data


def _extract_olm_device(
    owner: str, dev_id: str, info: dict[str, Any]
) -> OlmDevice | None:
    keys = info.get("keys") or {}
    if not isinstance(keys, dict):
        return None

    norm_keys: dict[str, str] = {}
    for key, value in keys.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.startswith("curve25519:"):
            norm_keys["curve25519"] = value
        elif key.startswith("ed25519:"):
            norm_keys["ed25519"] = value

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
