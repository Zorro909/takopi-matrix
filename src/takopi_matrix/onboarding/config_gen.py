"""Configuration file generation."""

from __future__ import annotations


def _mask_token(token: str) -> str:
    """Mask an access token for display."""
    token = token.strip()
    if len(token) <= 12:
        return "*" * len(token)
    return f"{token[:9]}...{token[-5:]}"


def _toml_escape(value: str) -> str:
    """Escape a string for TOML."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _render_config(
    homeserver: str,
    user_id: str,
    access_token: str,
    room_ids: list[str],
    default_engine: str | None,
    *,
    send_startup_message: bool = True,
) -> str:
    """Render configuration as TOML string."""
    lines: list[str] = []
    if default_engine:
        lines.append(f'default_engine = "{_toml_escape(default_engine)}"')
        lines.append("")
    lines.append('transport = "matrix"')
    lines.append("")
    lines.append("[transports.matrix]")
    lines.append(f'homeserver = "{_toml_escape(homeserver)}"')
    lines.append(f'user_id = "{_toml_escape(user_id)}"')
    lines.append(f'access_token = "{_toml_escape(access_token)}"')
    lines.append(f"room_ids = {room_ids!r}")
    if not send_startup_message:
        lines.append("send_startup_message = false")
    return "\n".join(lines) + "\n"
