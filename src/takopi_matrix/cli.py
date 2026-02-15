"""takopi-matrix CLI utilities (transport-local helper commands)."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .verify_device import run_verify_device


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="takopi-matrix")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    verify = sub.add_parser(
        "verify-device",
        help="SAS/emoji verify this Matrix device (E2EE helper)",
    )
    verify.add_argument(
        "--config",
        default="~/.takopi/takopi.toml",
        help="Path to takopi.toml (default: %(default)s)",
    )
    verify.add_argument(
        "--allow",
        action="append",
        default=[],
        help="Allowed sender user_id (repeatable). If omitted, accepts from any sender (unsafe).",
    )
    verify.add_argument(
        "--auto-confirm",
        dest="auto_confirm",
        action="store_true",
        default=True,
        help="Auto-confirm SAS (default). Still prints emojis/decimals.",
    )
    verify.add_argument(
        "--manual-confirm",
        dest="auto_confirm",
        action="store_false",
        help="Do not auto-confirm SAS; print emojis/decimals only.",
    )
    verify.add_argument(
        "--max-wait",
        type=int,
        default=30 * 60,
        help="Max seconds to wait for verification events (0 = no timeout). Default: %(default)s",
    )
    verify.add_argument(
        "--debug-events",
        action="store_true",
        default=False,
        help="Print additional event/debug information.",
    )
    verify.add_argument(
        "--send-plaintext",
        dest="send_plaintext",
        action="store_true",
        default=True,
        help="Send plaintext to-device verification events (default).",
    )
    verify.add_argument(
        "--no-send-plaintext",
        dest="send_plaintext",
        action="store_false",
        help="Do not send plaintext to-device verification events.",
    )
    verify.add_argument(
        "--send-encrypted",
        dest="send_encrypted",
        action="store_true",
        default=True,
        help="Also try sending Olm-encrypted to-device verification events (default).",
    )
    verify.add_argument(
        "--no-send-encrypted",
        dest="send_encrypted",
        action="store_false",
        help="Do not attempt to send Olm-encrypted verification events.",
    )
    verify.add_argument(
        "--initiate-to",
        default="",
        help="Initiate verification to a user_id (experimental; responder-only is usually more reliable).",
    )
    verify.add_argument(
        "--initiate-device-id",
        action="append",
        default=[],
        help="Only target these device IDs when using --initiate-to (repeatable).",
    )
    verify.add_argument(
        "--verify-all",
        action="store_true",
        default=False,
        help="In initiator mode, keep running until all targeted devices are verified.",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "verify-device":
        rc = run_verify_device(
            config_path=args.config,
            allowed_senders=set(args.allow),
            auto_confirm=bool(args.auto_confirm),
            max_wait_seconds=int(args.max_wait),
            debug_events=bool(args.debug_events),
            send_plaintext=bool(args.send_plaintext),
            send_encrypted=bool(args.send_encrypted),
            initiate_to=str(args.initiate_to).strip(),
            initiate_device_ids=set(args.initiate_device_id),
            verify_all=bool(args.verify_all),
        )
        raise SystemExit(rc)

    parser.error(f"unknown command: {args.cmd}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1:])
