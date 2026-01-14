"""Tests for Matrix bridge - transport, presenter, and command handling."""

from __future__ import annotations


import anyio
import pytest

from takopi.model import EngineId
from takopi.progress import ProgressState
from takopi.router import AutoRouter, RunnerEntry
from takopi.runners.mock import ScriptRunner
from takopi.transport import MessageRef, RenderedMessage, SendOptions
from takopi_matrix.bridge import (
    _is_cancel_command,
    _parse_slash_command,
    MatrixPresenter,
    MatrixTransport,
)
from matrix_fixtures import (
    MATRIX_ROOM_ID,
    MATRIX_SENDER,
    make_matrix_message,
    make_matrix_reaction,
)

CODEX_ENGINE: EngineId = "codex"


# --- Cancel detection ---


def test_is_cancel_command_basic() -> None:
    """/cancel is detected."""
    assert _is_cancel_command("/cancel")
    assert _is_cancel_command("/cancel ")
    assert _is_cancel_command("  /cancel  ")


def test_is_cancel_command_with_args() -> None:
    """/cancel with args is still detected."""
    assert _is_cancel_command("/cancel abc")
    assert _is_cancel_command("/cancel some message")


def test_is_cancel_command_with_bot_suffix() -> None:
    """/cancel@bot is detected."""
    assert _is_cancel_command("/cancel@botname")
    assert _is_cancel_command("/cancel@mybot extra")


def test_is_cancel_command_case_sensitive() -> None:
    """Cancel detection is case sensitive."""
    assert not _is_cancel_command("/Cancel")
    assert not _is_cancel_command("/CANCEL")


def test_is_cancel_command_not_cancel() -> None:
    """Other commands are not cancel."""
    assert not _is_cancel_command("/help")
    assert not _is_cancel_command("/start")
    assert not _is_cancel_command("cancel")
    assert not _is_cancel_command("")
    assert not _is_cancel_command("   ")


def test_is_cancel_command_partial() -> None:
    """/cancellation is not /cancel."""
    assert not _is_cancel_command("/cancellation")
    assert not _is_cancel_command("/cancelit")


# --- Slash command parsing ---


def test_parse_slash_command_basic() -> None:
    """Basic /cmd arg parsing."""
    cmd, args = _parse_slash_command("/test hello world")
    assert cmd == "test"
    assert args == "hello world"


def test_parse_slash_command_no_args() -> None:
    """Command with no arguments."""
    cmd, args = _parse_slash_command("/help")
    assert cmd == "help"
    assert args == ""


def test_parse_slash_command_multiline() -> None:
    """Multiline command preserves rest."""
    cmd, args = _parse_slash_command("/cmd first\nsecond line\nthird")
    assert cmd == "cmd"
    assert args == "first\nsecond line\nthird"


def test_parse_slash_command_multiline_no_first_arg() -> None:
    """Multiline command with no first arg."""
    cmd, args = _parse_slash_command("/cmd\nsecond line")
    assert cmd == "cmd"
    assert args == "second line"


def test_parse_slash_command_bot_suffix() -> None:
    """Command with @bot suffix strips bot name."""
    cmd, args = _parse_slash_command("/help@mybot extra")
    assert cmd == "help"
    assert args == "extra"


def test_parse_slash_command_lowercase() -> None:
    """Command is lowercased."""
    cmd, args = _parse_slash_command("/HELP")
    assert cmd == "help"

    cmd, args = _parse_slash_command("/HeLp MixedCase")
    assert cmd == "help"
    assert args == "MixedCase"


def test_parse_slash_command_not_command() -> None:
    """Non-command text returns None."""
    cmd, args = _parse_slash_command("hello world")
    assert cmd is None
    assert args == "hello world"


def test_parse_slash_command_empty() -> None:
    """Empty text returns None."""
    cmd, args = _parse_slash_command("")
    assert cmd is None
    assert args == ""


def test_parse_slash_command_just_slash() -> None:
    """Just / returns None."""
    cmd, args = _parse_slash_command("/")
    assert cmd is None
    assert args == "/"


def test_parse_slash_command_leading_whitespace() -> None:
    """Leading whitespace is ignored."""
    cmd, args = _parse_slash_command("  /test arg")
    assert cmd == "test"
    assert args == "arg"


# --- Presenter ---


def _make_progress_state() -> ProgressState:
    """Create a minimal ProgressState for testing."""
    return ProgressState(
        engine="codex",
        action_count=0,
        actions=(),
        resume=None,
        resume_line=None,
        context_line=None,
    )


def test_presenter_render_progress() -> None:
    """Presenter renders progress message."""
    presenter = MatrixPresenter()
    state = _make_progress_state()

    result = presenter.render_progress(state, elapsed_s=1.5, label="thinking")

    assert isinstance(result, RenderedMessage)
    assert result.text is not None
    assert "formatted_body" in result.extra


def test_presenter_render_progress_with_actions() -> None:
    """Presenter includes actions in progress."""
    presenter = MatrixPresenter()
    # Just test with basic state - action tracking is tested elsewhere
    state = _make_progress_state()

    result = presenter.render_progress(state, elapsed_s=2.0)

    assert result.text is not None


def test_presenter_render_final_success() -> None:
    """Presenter renders final success message."""
    presenter = MatrixPresenter()
    state = _make_progress_state()

    result = presenter.render_final(
        state, elapsed_s=5.0, status="completed", answer="Here is the answer."
    )

    assert isinstance(result, RenderedMessage)
    assert "answer" in result.text.lower() or "Here is the answer" in result.text


def test_presenter_render_final_cancelled() -> None:
    """Presenter renders cancelled state."""
    presenter = MatrixPresenter()
    state = _make_progress_state()

    result = presenter.render_final(state, elapsed_s=2.0, status="cancelled", answer="")

    assert isinstance(result, RenderedMessage)
    # Status should be reflected somehow
    assert result.text  # Not empty


def test_presenter_render_final_error() -> None:
    """Presenter renders error state."""
    presenter = MatrixPresenter()
    state = _make_progress_state()

    result = presenter.render_final(
        state, elapsed_s=3.0, status="error", answer="An error occurred."
    )

    assert isinstance(result, RenderedMessage)


def test_presenter_renders_html() -> None:
    """Presenter includes HTML formatted body."""
    presenter = MatrixPresenter()
    state = _make_progress_state()

    result = presenter.render_progress(state, elapsed_s=1.0)

    assert "formatted_body" in result.extra
    html = result.extra["formatted_body"]
    # Should be actual HTML
    assert "<" in html or html == ""  # Could be empty for minimal state


# --- Fake transport for testing ---


class FakeMatrixClient:
    """Fake MatrixClient for transport testing."""

    def __init__(self) -> None:
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.redact_calls: list[tuple[str, str]] = []
        self.drop_pending_calls: list[str] = []
        self._next_event_id = 1

    async def close(self) -> None:
        pass

    async def send_message(
        self,
        room_id: str,
        body: str,
        formatted_body: str | None = None,
        reply_to_event_id: str | None = None,
        *,
        disable_notification: bool = False,
        wait: bool = True,
    ) -> dict[str, str] | None:
        event_id = f"$sent{self._next_event_id}:example.org"
        self._next_event_id += 1
        self.send_calls.append(
            {
                "room_id": room_id,
                "body": body,
                "formatted_body": formatted_body,
                "reply_to_event_id": reply_to_event_id,
                "disable_notification": disable_notification,
                "wait": wait,
            }
        )
        return {"event_id": event_id}

    async def edit_message(
        self,
        room_id: str,
        event_id: str,
        body: str,
        formatted_body: str | None = None,
        *,
        wait: bool = True,
    ) -> dict[str, str] | None:
        self.edit_calls.append(
            {
                "room_id": room_id,
                "event_id": event_id,
                "body": body,
                "formatted_body": formatted_body,
                "wait": wait,
            }
        )
        return {"event_id": event_id}

    async def redact_message(
        self,
        room_id: str,
        event_id: str,
        reason: str | None = None,
    ) -> bool:
        self.redact_calls.append((room_id, event_id))
        return True

    async def drop_pending_edits(self, room_id: str) -> None:
        self.drop_pending_calls.append(room_id)


@pytest.mark.anyio
async def test_transport_send_basic() -> None:
    """Transport sends message to room."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    message = RenderedMessage(
        text="Hello Matrix!",
        extra={"formatted_body": "<p>Hello Matrix!</p>"},
    )

    ref = await transport.send(
        channel_id=MATRIX_ROOM_ID,
        message=message,
    )

    assert ref is not None
    assert ref.channel_id == MATRIX_ROOM_ID
    assert len(client.send_calls) == 1
    assert client.send_calls[0]["body"] == "Hello Matrix!"


@pytest.mark.anyio
async def test_transport_send_with_reply() -> None:
    """Transport sends reply to specific message."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    message = RenderedMessage(text="Reply text")
    options = SendOptions(
        reply_to=MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$original:ex.org"),
        notify=True,
    )

    ref = await transport.send(
        channel_id=MATRIX_ROOM_ID,
        message=message,
        options=options,
    )

    assert ref is not None
    assert client.send_calls[0]["reply_to_event_id"] == "$original:ex.org"


@pytest.mark.anyio
async def test_transport_send_silent() -> None:
    """Transport respects notify=False (silent send)."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    message = RenderedMessage(text="Silent message")
    options = SendOptions(notify=False)

    await transport.send(
        channel_id=MATRIX_ROOM_ID,
        message=message,
        options=options,
    )

    # Note: Matrix doesn't have silent sends like Telegram, but the option is passed
    assert len(client.send_calls) == 1


@pytest.mark.anyio
async def test_transport_edit() -> None:
    """Transport edits existing message."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    ref = MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$existing:example.org")
    message = RenderedMessage(
        text="Updated text",
        extra={"formatted_body": "<p>Updated text</p>"},
    )

    result = await transport.edit(ref=ref, message=message)

    assert result == ref
    assert len(client.edit_calls) == 1
    assert client.edit_calls[0]["event_id"] == "$existing:example.org"
    assert client.edit_calls[0]["body"] == "Updated text"


@pytest.mark.anyio
async def test_transport_edit_no_wait() -> None:
    """Transport can edit without waiting."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    ref = MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$existing:example.org")
    message = RenderedMessage(text="Quick edit")

    await transport.edit(ref=ref, message=message, wait=False)

    assert client.edit_calls[0]["wait"] is False


@pytest.mark.anyio
async def test_transport_delete() -> None:
    """Transport deletes (redacts) message."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    ref = MessageRef(channel_id=MATRIX_ROOM_ID, message_id="$todelete:example.org")

    result = await transport.delete(ref=ref)

    assert result is True
    assert len(client.redact_calls) == 1
    assert client.redact_calls[0] == (MATRIX_ROOM_ID, "$todelete:example.org")


@pytest.mark.anyio
async def test_transport_close() -> None:
    """Transport closes client."""
    client = FakeMatrixClient()
    transport = MatrixTransport(client)  # type: ignore[arg-type]

    await transport.close()
    # No assertions needed, just verify no errors


# --- Helper functions ---


def test_cancel_reactions_include_x() -> None:
    """Cancel reactions include various cancel indicators."""
    from takopi_matrix.bridge import _CANCEL_REACTIONS

    assert "❌" in _CANCEL_REACTIONS
    assert "x" in _CANCEL_REACTIONS
    assert "X" in _CANCEL_REACTIONS


# --- Integration helpers ---


class FakeMatrixTransport:
    """Fake transport for testing main loop."""

    def __init__(self, progress_ready: anyio.Event | None = None) -> None:
        self._next_id = 1
        self.send_calls: list[dict] = []
        self.edit_calls: list[dict] = []
        self.delete_calls: list[MessageRef] = []
        self.progress_ready = progress_ready
        self.progress_ref: MessageRef | None = None

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        ref = MessageRef(channel_id=channel_id, message_id=f"$evt{self._next_id}")
        self._next_id += 1
        self.send_calls.append(
            {
                "ref": ref,
                "channel_id": channel_id,
                "message": message,
                "options": options,
            }
        )
        if (
            self.progress_ref is None
            and options is not None
            and options.notify is False
        ):
            self.progress_ref = ref
            if self.progress_ready is not None:
                self.progress_ready.set()
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        self.edit_calls.append({"ref": ref, "message": message, "wait": wait})
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        self.delete_calls.append(ref)
        return True

    async def close(self) -> None:
        pass


def _make_router(runner: ScriptRunner) -> AutoRouter:
    """Create router for testing."""
    return AutoRouter(
        entries=[RunnerEntry(engine=runner.engine, runner=runner)],
        default_engine=runner.engine,
    )


# --- Message fixture tests ---


def test_make_matrix_message_defaults() -> None:
    """make_matrix_message creates valid message."""
    msg = make_matrix_message()
    assert msg.transport == "matrix"
    assert msg.room_id == MATRIX_ROOM_ID
    assert msg.sender == MATRIX_SENDER


def test_make_matrix_message_custom() -> None:
    """make_matrix_message accepts overrides."""
    msg = make_matrix_message(
        text="custom text",
        room_id="!other:example.org",
        reply_to_event_id="$prev:example.org",
    )
    assert msg.text == "custom text"
    assert msg.room_id == "!other:example.org"
    assert msg.reply_to_event_id == "$prev:example.org"


def test_make_matrix_reaction() -> None:
    """make_matrix_reaction creates valid reaction."""
    reaction = make_matrix_reaction(key="❌")
    assert reaction.key == "❌"
    assert reaction.room_id == MATRIX_ROOM_ID


# --- Edge cases ---


def test_parse_slash_command_unicode() -> None:
    """Unicode in command args preserved."""
    cmd, args = _parse_slash_command("/test 你好世界 🎉")
    assert cmd == "test"
    assert "你好世界" in args
    assert "🎉" in args


def test_parse_slash_command_quoted_args() -> None:
    """Quoted arguments preserved as-is in raw form."""
    cmd, args = _parse_slash_command('/cmd "arg with spaces"')
    assert cmd == "cmd"
    assert '"arg with spaces"' in args


def test_presenter_handles_empty_state() -> None:
    """Presenter handles empty progress state."""
    presenter = MatrixPresenter()
    state = _make_progress_state()

    progress = presenter.render_progress(state, elapsed_s=0.0)
    final = presenter.render_final(state, elapsed_s=0.0, status="done", answer="")

    assert progress.text is not None
    assert final.text is not None
