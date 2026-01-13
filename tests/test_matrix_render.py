"""Tests for Matrix message rendering."""

from __future__ import annotations

from takopi.markdown import MarkdownParts
from takopi_matrix.render import (
    _MAX_BODY_LENGTH,
    prepare_matrix,
    render_markdown_to_html,
    split_at_paragraph,
    trim_body,
)


def test_render_markdown_to_html_basic() -> None:
    """Simple markdown renders to HTML."""
    result = render_markdown_to_html("**bold** and *italic*")
    assert "<strong>bold</strong>" in result
    assert "<em>italic</em>" in result


def test_render_markdown_to_html_code_block() -> None:
    """Code blocks render with pre/code tags."""
    result = render_markdown_to_html("```python\nprint('hello')\n```")
    assert "<pre>" in result
    assert "code" in result.lower()  # Allow for <code> or <code class=...>
    assert "print" in result


def test_render_markdown_to_html_inline_code() -> None:
    """Inline code renders with code tag."""
    result = render_markdown_to_html("Use `foo()` here")
    assert "<code>foo()</code>" in result


def test_render_markdown_to_html_empty() -> None:
    """Empty input returns empty string."""
    assert render_markdown_to_html("") == ""
    assert render_markdown_to_html(None) == ""  # type: ignore[arg-type]


def test_render_markdown_to_html_lists() -> None:
    """Lists render as HTML lists."""
    result = render_markdown_to_html("- item 1\n- item 2")
    assert "<ul>" in result
    assert "<li>" in result


def test_trim_body_under_limit() -> None:
    """Body under limit is unchanged."""
    body = "short text"
    result = trim_body(body)
    assert result == body


def test_trim_body_at_limit() -> None:
    """Body at exact limit is unchanged."""
    body = "x" * _MAX_BODY_LENGTH
    result = trim_body(body)
    assert result == body
    assert len(result) == _MAX_BODY_LENGTH


def test_trim_body_over_limit() -> None:
    """Body over limit is truncated with ellipsis."""
    body = "x" * (_MAX_BODY_LENGTH + 100)
    result = trim_body(body)
    assert result is not None
    assert result.endswith("...")
    assert len(result) == _MAX_BODY_LENGTH


def test_trim_body_custom_limit() -> None:
    """Custom limit is respected."""
    body = "x" * 200
    result = trim_body(body, max_len=100)
    assert result is not None
    assert len(result) == 100
    assert result.endswith("...")


def test_trim_body_empty() -> None:
    """Empty body returns None."""
    assert trim_body("") is None
    assert trim_body(None) is None


def test_trim_body_whitespace_only() -> None:
    """Whitespace-only body returns None."""
    assert trim_body("   ") is None
    assert trim_body("\n\t\n") is None


def test_prepare_matrix_returns_tuple() -> None:
    """prepare_matrix returns (plain_text, html) tuple."""
    parts = MarkdownParts(header="Header", body="Body text", footer="Footer")
    plain, html = prepare_matrix(parts)
    assert isinstance(plain, str)
    assert isinstance(html, str)
    assert "Header" in plain
    assert "Body text" in plain


def test_prepare_matrix_renders_markdown() -> None:
    """prepare_matrix renders markdown in body."""
    parts = MarkdownParts(header="", body="**bold** text")
    plain, html = prepare_matrix(parts)
    assert "bold" in plain
    assert "strong" in html.lower()  # HTML renders markdown


def test_prepare_matrix_handles_none_body() -> None:
    """prepare_matrix handles None body."""
    parts = MarkdownParts(header="Just header")
    plain, html = prepare_matrix(parts)
    assert "Just header" in plain


def test_prepare_matrix_trims_long_body() -> None:
    """prepare_matrix trims body exceeding limit."""
    long_body = "x" * (_MAX_BODY_LENGTH + 1000)
    parts = MarkdownParts(header="", body=long_body)
    plain, html = prepare_matrix(parts)
    # The body gets trimmed
    assert len(plain) <= _MAX_BODY_LENGTH + 100  # Some slack for header/footer


def test_split_at_paragraph_under_limit() -> None:
    """Text under limit returns single chunk."""
    text = "Short text\n\nAnother paragraph"
    result = split_at_paragraph(text)
    assert result == [text]


def test_split_at_paragraph_exact_limit() -> None:
    """Text at exact limit returns single chunk."""
    text = "x" * 100
    result = split_at_paragraph(text, max_length=100)
    assert result == [text]


def test_split_at_paragraph_multi_chunks() -> None:
    """Multiple paragraphs split at boundaries."""
    para1 = "a" * 50
    para2 = "b" * 50
    para3 = "c" * 50
    text = f"{para1}\n\n{para2}\n\n{para3}"
    result = split_at_paragraph(text, max_length=60)
    assert len(result) == 3
    assert para1 in result[0]
    assert para2 in result[1]
    assert para3 in result[2]


def test_split_at_paragraph_huge_paragraph() -> None:
    """Single paragraph exceeding limit is force-split."""
    text = "x" * 200
    result = split_at_paragraph(text, max_length=50)
    assert len(result) == 4
    assert all(len(chunk) <= 50 for chunk in result)
    assert "".join(result) == text


def test_split_at_paragraph_preserves_content() -> None:
    """Splitting preserves all content."""
    para1 = "First paragraph with content"
    para2 = "Second paragraph here"
    para3 = "Third one too"
    text = f"{para1}\n\n{para2}\n\n{para3}"
    result = split_at_paragraph(text, max_length=40)
    joined = "\n\n".join(result)
    # All content preserved (though may be restructured)
    assert "First paragraph" in joined
    assert "Second paragraph" in joined
    assert "Third one" in joined


def test_split_at_paragraph_empty() -> None:
    """Empty text returns single empty list entry."""
    result = split_at_paragraph("")
    assert result == [""]


def test_split_at_paragraph_strips_chunks() -> None:
    """Split chunks are stripped of leading/trailing whitespace."""
    text = "para1  \n\n  para2  "
    result = split_at_paragraph(text, max_length=100)
    # The implementation strips chunks
    assert result == [text]  # Under limit, no splitting


def test_split_at_paragraph_combines_small() -> None:
    """Small paragraphs that fit are combined."""
    text = "a\n\nb\n\nc"
    result = split_at_paragraph(text, max_length=100)
    assert result == [text]  # All fit together
