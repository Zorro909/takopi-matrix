from __future__ import annotations

from pathlib import Path

import pytest

from takopi_matrix.cli import _build_parser
from takopi_matrix.verify_device import _extract_olm_device, _resolve_creds


def test_cli_parses_verify_device_args() -> None:
    parser = _build_parser()
    ns = parser.parse_args(
        [
            "verify-device",
            "--config",
            "/tmp/takopi.toml",
            "--allow",
            "@s:example.org",
            "--manual-confirm",
            "--max-wait",
            "123",
            "--no-send-encrypted",
            "--initiate-to",
            "@bot:example.org",
            "--initiate-device-id",
            "DEV1",
            "--initiate-retries",
            "5",
            "--initiate-retry-interval",
            "7",
            "--no-broadcast-request",
            "--verify-all",
        ]
    )
    assert ns.cmd == "verify-device"
    assert ns.config == "/tmp/takopi.toml"
    assert ns.allow == ["@s:example.org"]
    assert ns.auto_confirm is False
    assert ns.max_wait == 123
    assert ns.send_encrypted is False
    assert ns.initiate_to == "@bot:example.org"
    assert ns.initiate_device_id == ["DEV1"]
    assert ns.initiate_retries == 5
    assert ns.initiate_retry_interval == 7
    assert ns.broadcast_request is False
    assert ns.verify_all is True


def test_cli_defaults_broadcast_to_auto() -> None:
    parser = _build_parser()
    ns = parser.parse_args(["verify-device"])
    assert ns.broadcast_request is None


def test_resolve_creds_uses_whoami_device_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "takopi.toml"
    cfg.write_text(
        """
[transports.matrix]
homeserver = "https://hs.example"
user_id = "@bot:hs.example"
access_token = "TOKEN"
crypto_store_path = "/tmp/matrix_crypto.db"
""".lstrip(),
        encoding="utf-8",
    )

    def fake_whoami(hs: str, token: str) -> dict[str, str]:
        assert hs == "https://hs.example"
        assert token == "TOKEN"
        return {"user_id": "@bot:hs.example", "device_id": "DEV123"}

    monkeypatch.setattr("takopi_matrix.verify_device._whoami", fake_whoami)
    creds = _resolve_creds(cfg)
    assert creds.user_id == "@bot:hs.example"
    assert creds.device_id == "DEV123"
    assert creds.store_dir.as_posix() == "/tmp"


def test_extract_olm_device_requires_keys() -> None:
    info = {"keys": {"curve25519:DEV": "c"}}
    assert _extract_olm_device("@u:hs", "DEV", info) is None
