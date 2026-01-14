"""Progress tracking for takopi-matrix."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from takopi.api import Action, ActionEvent, ResumeToken, StartedEvent


@dataclass(frozen=True, slots=True)
class ActionState:
    action: Action
    phase: str
    ok: bool | None
    display_phase: str
    completed: bool
    first_seen: int
    last_update: int


@dataclass(frozen=True, slots=True)
class ProgressState:
    engine: str
    action_count: int
    actions: tuple[ActionState, ...]
    resume: ResumeToken | None
    resume_line: str | None
    context_line: str | None


class ProgressTracker:
    def __init__(self, *, engine: str) -> None:
        self.engine = engine
        self.resume: ResumeToken | None = None
        self.action_count = 0
        self._actions: dict[str, ActionState] = {}
        self._seq = 0

    def note_event(self, event: StartedEvent | ActionEvent) -> bool:
        if isinstance(event, StartedEvent):
            self.resume = event.resume
            return True
        if isinstance(event, ActionEvent):
            action = event.action
            if action.kind == "turn" or not action.id:
                return False
            action_id = str(action.id)
            existing = self._actions.get(action_id)
            completed = event.phase == "completed"
            has_open = existing is not None and not existing.completed
            is_update = event.phase == "updated" or (event.phase == "started" and has_open)
            display_phase = "updated" if is_update and not completed else event.phase
            self._seq += 1
            self._actions[action_id] = ActionState(
                action=action,
                phase=event.phase,
                ok=event.ok,
                display_phase=display_phase,
                completed=completed,
                first_seen=existing.first_seen if existing else self._seq,
                last_update=self._seq,
            )
            if existing is None:
                self.action_count += 1
            return True
        return False

    def set_resume(self, resume: ResumeToken | None) -> None:
        if resume is not None:
            self.resume = resume

    def snapshot(
        self,
        *,
        resume_formatter: Callable[[ResumeToken], str] | None = None,
        context_line: str | None = None,
    ) -> ProgressState:
        resume_line = None
        if self.resume and resume_formatter:
            resume_line = resume_formatter(self.resume)
        actions = tuple(sorted(self._actions.values(), key=lambda a: a.first_seen))
        return ProgressState(
            engine=self.engine,
            action_count=self.action_count,
            actions=actions,
            resume=self.resume,
            resume_line=resume_line,
            context_line=context_line,
        )
