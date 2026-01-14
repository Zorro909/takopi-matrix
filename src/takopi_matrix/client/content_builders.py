"""Content builders for Matrix message formatting."""

from __future__ import annotations

from typing import Any


def _build_reply_content(
    body: str,
    formatted_body: str | None,
    reply_to_event_id: str,
) -> dict[str, Any]:
    """Build content with m.relates_to for replies."""
    content: dict[str, Any] = {
        "msgtype": "m.text",
        "body": body,
        "m.relates_to": {
            "m.in_reply_to": {"event_id": reply_to_event_id},
        },
    }
    if formatted_body:
        content["format"] = "org.matrix.custom.html"
        content["formatted_body"] = formatted_body
    return content


def _build_edit_content(
    body: str,
    formatted_body: str | None,
    original_event_id: str,
) -> dict[str, Any]:
    """Build content with m.relates_to for edits (m.replace)."""
    new_content: dict[str, Any] = {
        "msgtype": "m.text",
        "body": body,
    }
    if formatted_body:
        new_content["format"] = "org.matrix.custom.html"
        new_content["formatted_body"] = formatted_body

    return {
        "msgtype": "m.text",
        "body": f"* {body}",
        "m.new_content": new_content,
        "m.relates_to": {
            "rel_type": "m.replace",
            "event_id": original_event_id,
        },
    }
