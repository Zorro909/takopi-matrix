"""Matrix presenter for rendering progress and final messages."""

from __future__ import annotations

from takopi.api import RenderedMessage
from takopi.markdown import MarkdownFormatter
from takopi.progress import ProgressState

from ..render import prepare_matrix


class MatrixPresenter:
    """Renders progress and final messages with Matrix HTML formatting."""

    def __init__(self, *, formatter: MarkdownFormatter | None = None) -> None:
        self._formatter = formatter or MarkdownFormatter()

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        parts = self._formatter.render_progress_parts(
            state, elapsed_s=elapsed_s, label=label
        )
        text, formatted_body = prepare_matrix(parts)
        return RenderedMessage(text=text, extra={"formatted_body": formatted_body})

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        parts = self._formatter.render_final_parts(
            state, elapsed_s=elapsed_s, status=status, answer=answer
        )
        text, formatted_body = prepare_matrix(parts)
        return RenderedMessage(text=text, extra={"formatted_body": formatted_body})
