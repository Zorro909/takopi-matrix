"""Command parsing utilities."""

from __future__ import annotations

import shlex


def normalize_slash_prefix(text: str) -> str:
    """Normalize slash-prefixed text.

    Matrix clients often require `//cmd` so the sent text is `/cmd`.
    Accept both forms by collapsing only the first leading slash.
    """
    stripped = text.lstrip()
    if not stripped.startswith("//"):
        return text
    leading_ws = len(text) - len(stripped)
    return f"{text[:leading_ws]}{stripped[1:]}"


def parse_slash_command(text: str) -> tuple[str | None, str]:
    """Parse a slash command from text, returning (command_id, args_text).

    Args:
        text: The message text to parse.

    Returns:
        A tuple of (command_id, args_text) where command_id is None if
        the text is not a slash command.
    """
    stripped = normalize_slash_prefix(text).lstrip()
    if not stripped.startswith("/"):
        return None, text
    lines = stripped.splitlines()
    if not lines:
        return None, text
    first_line = lines[0]
    token, _, rest = first_line.partition(" ")
    command = token[1:]
    if not command:
        return None, text
    if "@" in command:
        command = command.split("@", 1)[0]
    args_text = rest
    if len(lines) > 1:
        tail = "\n".join(lines[1:])
        args_text = f"{args_text}\n{tail}" if args_text else tail
    return command.lower(), args_text


def split_command_args(text: str) -> tuple[str, ...]:
    """Split command arguments using shell-like parsing.

    Args:
        text: The arguments text to split.

    Returns:
        A tuple of argument strings.
    """
    if not text.strip():
        return ()
    try:
        return tuple(shlex.split(text))
    except ValueError:
        return tuple(text.split())
