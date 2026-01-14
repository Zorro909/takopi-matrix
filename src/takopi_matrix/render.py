from __future__ import annotations

from markdown_it import MarkdownIt

from .markdown import MarkdownParts, assemble_markdown_parts

_MD_RENDERER = MarkdownIt("commonmark", {"html": False})

_MAX_BODY_LENGTH = 32000


def render_markdown_to_html(md: str) -> str:
    """Render markdown to Matrix-compatible HTML."""
    return _MD_RENDERER.render(md or "")


def trim_body(body: str | None, max_len: int = _MAX_BODY_LENGTH) -> str | None:
    """Trim body for Matrix's message size limits."""
    if not body:
        return None
    if len(body) > max_len:
        body = body[: max_len - 3] + "..."
    return body if body.strip() else None


def prepare_matrix(parts: MarkdownParts) -> tuple[str, str]:
    """
    Prepare message for Matrix.

    Returns (plain_text, formatted_body_html).
    """
    trimmed = MarkdownParts(
        header=parts.header or "",
        body=trim_body(parts.body),
        footer=parts.footer,
    )
    plain_text = assemble_markdown_parts(trimmed)
    formatted_html = render_markdown_to_html(plain_text)
    return plain_text, formatted_html


def split_at_paragraph(text: str, max_length: int = _MAX_BODY_LENGTH) -> list[str]:
    """
    Split long text at paragraph boundaries.

    This is used for very long AI responses that exceed Matrix's limits.
    """
    if len(text) <= max_length:
        return [text]

    parts: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 <= max_length:
            current = f"{current}\n\n{paragraph}" if current else paragraph
        else:
            if current:
                parts.append(current.strip())
            if len(paragraph) > max_length:
                while len(paragraph) > max_length:
                    parts.append(paragraph[:max_length])
                    paragraph = paragraph[max_length:]
                current = paragraph
            else:
                current = paragraph

    if current.strip():
        parts.append(current.strip())

    return parts
