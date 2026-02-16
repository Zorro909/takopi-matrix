from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from takopi.api import get_logger

logger = get_logger("takopi_matrix.verify_device")


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
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            raise KeyError("Missing config key: " + ".".join(keys))
        cur = cur[key]
    return cur


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _whoami(homeserver: str, token: str) -> dict[str, Any]:
    hs = homeserver.rstrip("/")
    response = httpx.get(
        f"{hs}/_matrix/client/v3/account/whoami",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20.0,
    )
    if response.status_code == 401:
        raise RuntimeError("whoami unauthorized (401)")
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("whoami returned non-object JSON")
    return data


def _resolve_creds(config_path: Path) -> _MatrixCreds:
    cfg = _load_takopi_toml(config_path)
    matrix_config = _cfg_get(cfg, "transports", "matrix")
    if not isinstance(matrix_config, dict):
        raise TypeError("transports.matrix must be a table/dict")

    homeserver = str(matrix_config.get("homeserver") or "").strip().rstrip("/")
    user_id = str(matrix_config.get("user_id") or "").strip()
    cfg_access_token = str(matrix_config.get("access_token") or "").strip()
    cfg_device_id = str(matrix_config.get("device_id") or "").strip()

    if not homeserver:
        raise ValueError("Missing transports.matrix.homeserver")
    if not user_id:
        raise ValueError("Missing transports.matrix.user_id")
    if (
        not cfg_access_token
        and not _env("matrix_access_token")
        and not _env("MATRIX_ACCESS_TOKEN")
    ):
        raise ValueError(
            "Missing transports.matrix.access_token (or env matrix_access_token)"
        )

    env_access_token = _env("matrix_access_token") or _env("MATRIX_ACCESS_TOKEN")
    env_device_id = _env("matrix_device_id") or _env("MATRIX_DEVICE_ID")

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

    store_dir = Path.home() / ".takopi"
    raw_crypto_store = matrix_config.get("crypto_store_path")
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
