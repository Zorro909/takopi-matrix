"""Tests for markdown.py - Markdown formatting."""

from __future__ import annotations

from typing import Any, cast

from takopi.api import Action, ProgressState
from takopi.progress import ActionState
from takopi_matrix.markdown import (
    MarkdownParts,
    MarkdownFormatter,
    assemble_markdown_parts,
    _shorten,
    _format_elapsed,
    _format_header,
    _action_title,
    _action_line,
    STATUS,
)


# --- MarkdownParts tests ---


def test_assemble_markdown_parts_all() -> None:
    """Assemble parts with header, body, and footer."""
    parts = MarkdownParts(header="Header", body="Body", footer="Footer")
    result = assemble_markdown_parts(parts)
    assert result == "Header\n\nBody\n\nFooter"


def test_assemble_markdown_parts_header_only() -> None:
    """Assemble parts with header only."""
    parts = MarkdownParts(header="Header")
    result = assemble_markdown_parts(parts)
    assert result == "Header"


def test_assemble_markdown_parts_no_body() -> None:
    """Assemble parts without body."""
    parts = MarkdownParts(header="Header", footer="Footer")
    result = assemble_markdown_parts(parts)
    assert result == "Header\n\nFooter"


def test_assemble_markdown_parts_no_footer() -> None:
    """Assemble parts without footer."""
    parts = MarkdownParts(header="Header", body="Body")
    result = assemble_markdown_parts(parts)
    assert result == "Header\n\nBody"


# --- _shorten tests ---


def test_shorten_under_limit() -> None:
    """Text under limit is unchanged."""
    result = _shorten("short", 100)
    assert result == "short"


def test_shorten_over_limit() -> None:
    """Text over limit is shortened."""
    long_text = "a" * 200
    result = _shorten(long_text, 50)
    assert len(result) <= 50
    assert result.endswith("…")


def test_shorten_none_width() -> None:
    """Width=None means no shortening."""
    long_text = "a" * 1000
    result = _shorten(long_text, None)
    assert result == long_text


def test_shorten_exact_limit() -> None:
    """Text exactly at limit is unchanged."""
    text = "a" * 50
    result = _shorten(text, 50)
    assert result == text


# --- _format_elapsed tests ---


def test_format_elapsed_seconds() -> None:
    """Seconds only."""
    assert _format_elapsed(5.0) == "5s"
    assert _format_elapsed(59.0) == "59s"


def test_format_elapsed_minutes() -> None:
    """Minutes and seconds."""
    assert _format_elapsed(60.0) == "1m 00s"
    assert _format_elapsed(90.0) == "1m 30s"
    assert _format_elapsed(125.0) == "2m 05s"


def test_format_elapsed_hours() -> None:
    """Hours and minutes."""
    assert _format_elapsed(3600.0) == "1h 00m"
    assert _format_elapsed(3660.0) == "1h 01m"
    assert _format_elapsed(5400.0) == "1h 30m"


def test_format_elapsed_negative() -> None:
    """Negative values are clamped to 0."""
    assert _format_elapsed(-5.0) == "0s"


def test_format_elapsed_fractional() -> None:
    """Fractional seconds are truncated."""
    assert _format_elapsed(5.9) == "5s"


# --- _format_header tests ---


def test_format_header_basic() -> None:
    """Basic header with label, engine, and elapsed."""
    result = _format_header(5.0, None, "working", "codex")
    assert "working" in result
    assert "codex" in result
    assert "5s" in result


def test_format_header_with_step() -> None:
    """Header includes step number."""
    result = _format_header(5.0, 3, "working", "claude")
    assert "step 3" in result
    assert "claude" in result


def test_format_header_no_step() -> None:
    """Header without step number."""
    result = _format_header(10.0, None, "done", "codex")
    assert "step" not in result


# --- _action_title tests ---


def test_action_title_command() -> None:
    """Command action title is wrapped in backticks."""
    action = Action(id="a", kind="command", title="ls -la")
    result = _action_title(action, None)
    assert result == "`ls -la`"


def test_action_title_tool() -> None:
    """Tool action title has prefix."""
    action = Action(id="a", kind="tool", title="read_file")
    result = _action_title(action, None)
    assert result == "tool: read_file"


def test_action_title_web_search() -> None:
    """Web search action title has prefix."""
    action = Action(id="a", kind="web_search", title="python async io")
    result = _action_title(action, None)
    assert result == "searched: python async io"


def test_action_title_subagent() -> None:
    """Subagent action title has prefix."""
    action = Action(id="a", kind="subagent", title="research")
    result = _action_title(action, None)
    assert result == "subagent: research"


def test_action_title_file_change() -> None:
    """File change action title has prefix."""
    action = Action(id="a", kind="file_change", title="3 files")
    result = _action_title(action, None)
    assert result == "files: 3 files"


def test_action_title_unknown_kind() -> None:
    """Unknown kind just returns title."""
    action = Action(id="a", kind=cast(Any, "other"), title="something")
    result = _action_title(action, None)
    assert result == "something"


def test_action_title_shortened() -> None:
    """Long title is shortened."""
    action = Action(id="a", kind="command", title="x" * 500)
    result = _action_title(action, 50)
    assert len(result) <= 52  # backticks + shortened text


def test_action_title_none() -> None:
    """None title becomes empty string."""
    action = Action(id="a", kind="tool", title=cast(Any, None))
    result = _action_title(action, None)
    assert result == "tool: "


# --- _action_line tests ---


def test_action_line_running() -> None:
    """Running action shows running status."""
    action = Action(id="a", kind="command", title="running cmd")
    result = _action_line(action, "running", None, None)
    assert STATUS["running"] in result
    assert "running cmd" in result


def test_action_line_updated() -> None:
    """Updated action shows update status."""
    action = Action(id="a", kind="command", title="updating")
    result = _action_line(action, "updated", None, None)
    assert STATUS["update"] in result


def test_action_line_completed_ok() -> None:
    """Completed ok action shows done status."""
    action = Action(id="a", kind="command", title="done cmd")
    result = _action_line(action, "completed", True, None)
    assert STATUS["done"] in result


def test_action_line_completed_fail() -> None:
    """Completed failed action shows fail status."""
    action = Action(id="a", kind="command", title="failed cmd")
    result = _action_line(action, "completed", False, None)
    assert STATUS["fail"] in result


def test_action_line_completed_exit_code_zero() -> None:
    """Completed with exit code 0 shows done."""
    action = Action(id="a", kind="command", title="cmd", detail={"exit_code": 0})
    result = _action_line(action, "completed", None, None)
    assert STATUS["done"] in result
    assert "exit" not in result.lower()


def test_action_line_completed_exit_code_nonzero() -> None:
    """Completed with non-zero exit code shows fail with code."""
    action = Action(id="a", kind="command", title="cmd", detail={"exit_code": 1})
    result = _action_line(action, "completed", None, None)
    assert STATUS["fail"] in result
    assert "(exit 1)" in result


# --- MarkdownFormatter tests ---


def _make_progress_state(
    engine: str = "codex",
    action_count: int = 0,
    actions: tuple = (),
    resume_line: str | None = None,
    context_line: str | None = None,
) -> ProgressState:
    return ProgressState(
        engine=engine,
        action_count=action_count,
        actions=actions,
        resume=None,
        resume_line=resume_line,
        context_line=context_line,
    )


def test_formatter_render_progress_parts_minimal() -> None:
    """Minimal progress rendering."""
    formatter = MarkdownFormatter()
    state = _make_progress_state()
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    assert "working" in parts.header
    assert parts.body is None  # No actions
    assert parts.footer is None  # No context/resume


def test_formatter_render_progress_parts_with_label() -> None:
    """Progress with custom label."""
    formatter = MarkdownFormatter()
    state = _make_progress_state()
    parts = formatter.render_progress_parts(state, elapsed_s=5.0, label="thinking")

    assert "thinking" in parts.header


def test_formatter_render_progress_parts_with_context() -> None:
    """Progress with context line."""
    formatter = MarkdownFormatter()
    state = _make_progress_state(context_line="project: myproject")
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    assert parts.footer is not None
    assert "myproject" in parts.footer


def test_formatter_render_progress_parts_with_resume() -> None:
    """Progress with resume line."""
    formatter = MarkdownFormatter()
    state = _make_progress_state(resume_line="reply to resume")
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    assert parts.footer is not None
    assert "resume" in parts.footer


def test_formatter_render_progress_parts_with_actions() -> None:
    """Progress with actions."""
    action1 = Action(id="a", kind="command", title="ls")
    action2 = Action(id="a", kind="tool", title="read_file")
    snapshot1 = ActionState(
        action=action1,
        phase="completed",
        ok=True,
        display_phase="completed",
        completed=True,
        first_seen=0,
        last_update=0,
    )
    snapshot2 = ActionState(
        action=action2,
        phase="running",
        ok=None,
        display_phase="running",
        completed=False,
        first_seen=0,
        last_update=0,
    )

    formatter = MarkdownFormatter()
    state = _make_progress_state(action_count=2, actions=(snapshot1, snapshot2))
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    assert parts.body is not None
    assert "ls" in parts.body
    assert "read_file" in parts.body


def test_formatter_max_actions() -> None:
    """Formatter limits number of displayed actions."""
    actions = tuple(
        ActionState(
            action=Action(id="a", kind="command", title=f"cmd{i}"),
            phase="completed",
            ok=True,
            display_phase="completed",
            completed=True,
            first_seen=0,
            last_update=0,
        )
        for i in range(10)
    )
    formatter = MarkdownFormatter(max_actions=3)
    state = _make_progress_state(action_count=10, actions=actions)
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    # Should only show last 3 actions
    assert parts.body is not None
    assert "cmd7" in parts.body
    assert "cmd8" in parts.body
    assert "cmd9" in parts.body
    assert "cmd0" not in parts.body


def test_formatter_render_final_parts() -> None:
    """Final rendering with answer."""
    formatter = MarkdownFormatter()
    state = _make_progress_state()
    parts = formatter.render_final_parts(
        state, elapsed_s=10.0, status="done", answer="Here is the result."
    )

    assert "done" in parts.header
    assert parts.body == "Here is the result."


def test_formatter_render_final_parts_empty_answer() -> None:
    """Final rendering with empty answer."""
    formatter = MarkdownFormatter()
    state = _make_progress_state()
    parts = formatter.render_final_parts(
        state, elapsed_s=10.0, status="cancelled", answer=""
    )

    assert "cancelled" in parts.header
    assert parts.body is None


def test_formatter_render_final_parts_whitespace_answer() -> None:
    """Final rendering strips whitespace from answer."""
    formatter = MarkdownFormatter()
    state = _make_progress_state()
    parts = formatter.render_final_parts(
        state, elapsed_s=10.0, status="done", answer="  result  \n\n"
    )

    assert parts.body == "result"


def test_formatter_custom_command_width() -> None:
    """Formatter uses custom command width."""
    formatter = MarkdownFormatter(command_width=20)
    long_cmd = "x" * 100
    action = Action(id="a", kind="command", title=long_cmd)
    snapshot = ActionState(
        action=action,
        phase="running",
        ok=None,
        display_phase="running",
        completed=False,
        first_seen=0,
        last_update=0,
    )

    state = _make_progress_state(action_count=1, actions=(snapshot,))
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    assert parts.body is not None
    # Command should be shortened
    assert len(parts.body) < 100


def test_formatter_zero_max_actions() -> None:
    """max_actions=0 shows no actions."""
    actions = tuple(
        ActionState(
            action=Action(id="a", kind="command", title=f"cmd{i}"),
            phase="completed",
            ok=True,
            display_phase="completed",
            completed=True,
            first_seen=0,
            last_update=0,
        )
        for i in range(5)
    )
    formatter = MarkdownFormatter(max_actions=0)
    state = _make_progress_state(action_count=5, actions=actions)
    parts = formatter.render_progress_parts(state, elapsed_s=5.0)

    assert parts.body is None
