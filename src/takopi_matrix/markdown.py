"""Markdown formatting for takopi-matrix."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from takopi.api import Action, ProgressState

STATUS = {"running": "▸", "update": "↻", "done": "✓", "fail": "✗"}
HEADER_SEP = " · "
HARD_BREAK = "  \n"
MAX_CMD_LEN = 300


@dataclass(frozen=True, slots=True)
class MarkdownParts:
    header: str
    body: str | None = None
    footer: str | None = None


def assemble_markdown_parts(parts: MarkdownParts) -> str:
    return "\n\n".join(p for p in (parts.header, parts.body, parts.footer) if p)


def _shorten(text: str, width: int | None) -> str:
    if width is None or len(text) <= width:
        return text
    return textwrap.shorten(text, width=width, placeholder="…")


def _format_elapsed(elapsed_s: float) -> str:
    total = max(0, int(elapsed_s))
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _format_header(elapsed_s: float, step: int | None, label: str, engine: str) -> str:
    parts = [label, engine, _format_elapsed(elapsed_s)]
    if step:
        parts.append(f"step {step}")
    return HEADER_SEP.join(parts)


def _action_title(action: Action, width: int | None) -> str:
    title = _shorten(str(action.title or ""), width)
    kind = action.kind
    if kind == "command":
        return f"`{title}`"
    if kind == "tool":
        return f"tool: {title}"
    if kind == "web_search":
        return f"searched: {title}"
    if kind == "subagent":
        return f"subagent: {title}"
    if kind == "file_change":
        return f"files: {title}"
    return title


def _action_line(action: Action, phase: str, ok: bool | None, width: int | None) -> str:
    title = _action_title(action, width)
    if phase != "completed":
        status = STATUS["update"] if phase == "updated" else STATUS["running"]
        return f"{status} {title}"
    if ok is not None:
        status = STATUS["done"] if ok else STATUS["fail"]
    else:
        detail = action.detail or {}
        exit_code = detail.get("exit_code")
        status = (
            STATUS["fail"]
            if isinstance(exit_code, int) and exit_code != 0
            else STATUS["done"]
        )
    suffix = ""
    if isinstance((action.detail or {}).get("exit_code"), int):
        code = action.detail["exit_code"]
        if code != 0:
            suffix = f" (exit {code})"
    return f"{status} {title}{suffix}"


class MarkdownFormatter:
    def __init__(
        self, *, max_actions: int = 5, command_width: int | None = MAX_CMD_LEN
    ) -> None:
        self.max_actions = max(0, max_actions)
        self.command_width = command_width

    def render_progress_parts(
        self, state: ProgressState, *, elapsed_s: float, label: str = "working"
    ) -> MarkdownParts:
        header = _format_header(
            elapsed_s, state.action_count or None, label, state.engine
        )
        body = self._format_body(state)
        return MarkdownParts(
            header=header, body=body, footer=self._format_footer(state)
        )

    def render_final_parts(
        self, state: ProgressState, *, elapsed_s: float, status: str, answer: str
    ) -> MarkdownParts:
        header = _format_header(
            elapsed_s, state.action_count or None, status, state.engine
        )
        body = answer.strip() or None
        return MarkdownParts(
            header=header, body=body, footer=self._format_footer(state)
        )

    def _format_footer(self, state: ProgressState) -> str | None:
        lines = [line for line in (state.context_line, state.resume_line) if line]
        return HARD_BREAK.join(lines) if lines else None

    def _format_body(self, state: ProgressState) -> str | None:
        actions = list(state.actions)[-self.max_actions :] if self.max_actions else []
        lines = [
            _action_line(a.action, a.display_phase, a.ok, self.command_width)
            for a in actions
        ]
        return HARD_BREAK.join(lines) if lines else None
