"""Tests for bridge/events.py - event processing pipeline."""

from __future__ import annotations

import pytest
import anyio

from takopi.api import (
    ExecBridgeConfig,
    MessageRef,
    RenderedMessage,
    ResumeToken,
    RunningTask,
    SendOptions,
)
from takopi.runners.mock import Return, ScriptRunner
from takopi_matrix.bridge.events import (
    ExponentialBackoff,
    _send_plain,
    _wait_for_resume,
)
from matrix_fixtures import MATRIX_ROOM_ID, MATRIX_EVENT_ID


# --- ExponentialBackoff tests ---


def test_exponential_backoff_initial() -> None:
    """First call returns initial delay."""
    backoff = ExponentialBackoff(initial=1.0, maximum=60.0, multiplier=2.0)
    assert backoff.next() == 1.0


def test_exponential_backoff_doubles() -> None:
    """Subsequent calls double the delay."""
    backoff = ExponentialBackoff(initial=1.0, maximum=60.0, multiplier=2.0)
    assert backoff.next() == 1.0
    assert backoff.next() == 2.0
    assert backoff.next() == 4.0
    assert backoff.next() == 8.0


def test_exponential_backoff_caps_at_maximum() -> None:
    """Backoff is capped at maximum."""
    backoff = ExponentialBackoff(initial=1.0, maximum=10.0, multiplier=2.0)
    backoff.next()  # 1
    backoff.next()  # 2
    backoff.next()  # 4
    backoff.next()  # 8
    assert backoff.next() == 10.0  # capped at 10
    assert backoff.next() == 10.0  # stays at 10


def test_exponential_backoff_reset() -> None:
    """Reset returns to initial."""
    backoff = ExponentialBackoff(initial=1.0, maximum=60.0, multiplier=2.0)
    backoff.next()  # 1
    backoff.next()  # 2
    backoff.next()  # 4
    backoff.reset()
    assert backoff.next() == 1.0


def test_exponential_backoff_custom_multiplier() -> None:
    """Custom multiplier affects growth rate."""
    backoff = ExponentialBackoff(initial=1.0, maximum=100.0, multiplier=3.0)
    assert backoff.next() == 1.0
    assert backoff.next() == 3.0
    assert backoff.next() == 9.0
    assert backoff.next() == 27.0


def test_exponential_backoff_custom_initial() -> None:
    """Custom initial value is respected."""
    backoff = ExponentialBackoff(initial=5.0, maximum=100.0, multiplier=2.0)
    assert backoff.next() == 5.0
    assert backoff.next() == 10.0


# --- Fake transport for testing ---


class FakeTransport:
    """Fake transport for testing send functions."""

    def __init__(self) -> None:
        self.send_calls: list[dict] = []
        self._next_id = 1

    async def send(
        self,
        *,
        channel_id: int | str,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef:
        ref = MessageRef(channel_id=channel_id, message_id=f"$sent{self._next_id}")
        self._next_id += 1
        self.send_calls.append(
            {
                "channel_id": channel_id,
                "message": message,
                "options": options,
            }
        )
        return ref

    async def edit(
        self, *, ref: MessageRef, message: RenderedMessage, wait: bool = True
    ) -> MessageRef:
        return ref

    async def delete(self, *, ref: MessageRef) -> bool:
        return True

    async def close(self) -> None:
        pass


class FakePresenter:
    """Fake presenter for testing."""

    def render_progress(self, state, elapsed_s, label=None):
        return RenderedMessage(text=f"progress: {label}")

    def render_final(self, state, elapsed_s, status, answer):
        return RenderedMessage(text=f"final: {status} - {answer}")


def _make_exec_cfg(transport: FakeTransport) -> ExecBridgeConfig:
    """Create ExecBridgeConfig with fake transport."""
    return ExecBridgeConfig(
        transport=transport,
        presenter=FakePresenter(),
        final_notify=True,
    )


# --- _send_plain tests ---


@pytest.mark.anyio
async def test_send_plain_sends_message() -> None:
    """_send_plain sends a text message as reply."""
    transport = FakeTransport()
    exec_cfg = _make_exec_cfg(transport)

    await _send_plain(
        exec_cfg,
        room_id=MATRIX_ROOM_ID,
        reply_to_event_id=MATRIX_EVENT_ID,
        text="Hello, Matrix!",
    )

    assert len(transport.send_calls) == 1
    call = transport.send_calls[0]
    assert call["channel_id"] == MATRIX_ROOM_ID
    assert call["message"].text == "Hello, Matrix!"
    assert call["options"].reply_to.message_id == MATRIX_EVENT_ID
    assert call["options"].notify is True


@pytest.mark.anyio
async def test_send_plain_silent() -> None:
    """_send_plain respects notify=False."""
    transport = FakeTransport()
    exec_cfg = _make_exec_cfg(transport)

    await _send_plain(
        exec_cfg,
        room_id=MATRIX_ROOM_ID,
        reply_to_event_id=MATRIX_EVENT_ID,
        text="Silent message",
        notify=False,
    )

    assert len(transport.send_calls) == 1
    call = transport.send_calls[0]
    assert call["options"].notify is False


# --- _wait_for_resume tests ---


@pytest.mark.anyio
async def test_wait_for_resume_already_available() -> None:
    """Returns immediately if resume is already set."""
    task = RunningTask()
    task.resume = ResumeToken(engine="codex", value="resume-token-123")

    result = await _wait_for_resume(task)

    assert result == ResumeToken(engine="codex", value="resume-token-123")


@pytest.mark.anyio
async def test_wait_for_resume_becomes_available() -> None:
    """Waits for resume to become available."""
    task = RunningTask()

    async def set_resume_later():
        await anyio.sleep(0.01)
        task.resume = ResumeToken(engine="codex", value="delayed-token")
        task.resume_ready.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(set_resume_later)
        result = await _wait_for_resume(task)

    assert result == ResumeToken(engine="codex", value="delayed-token")


@pytest.mark.anyio
async def test_wait_for_resume_task_done_first() -> None:
    """Returns None if task completes before resume is set."""
    task = RunningTask()

    async def complete_task():
        await anyio.sleep(0.01)
        task.done.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(complete_task)
        result = await _wait_for_resume(task)

    assert result is None


# --- Integration-style tests ---


@pytest.mark.anyio
async def test_backoff_sequence_realistic() -> None:
    """Test realistic backoff sequence for reconnection."""
    backoff = ExponentialBackoff(initial=1.0, maximum=30.0, multiplier=2.0)

    delays = [backoff.next() for _ in range(10)]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 30.0, 30.0]


@pytest.mark.anyio
async def test_backoff_reset_after_success() -> None:
    """Backoff should reset after successful reconnection."""
    backoff = ExponentialBackoff(initial=1.0, maximum=60.0, multiplier=2.0)

    # Simulate failures
    backoff.next()  # 1
    backoff.next()  # 2
    backoff.next()  # 4

    # Success - reset
    backoff.reset()

    # Next failure starts fresh
    assert backoff.next() == 1.0


# --- _send_runner_unavailable tests ---


@pytest.mark.anyio
async def test_send_runner_unavailable_sends_error() -> None:
    """_send_runner_unavailable sends error message."""
    from takopi_matrix.bridge.events import _send_runner_unavailable

    transport = FakeTransport()
    exec_cfg = _make_exec_cfg(transport)
    runner = ScriptRunner([Return(answer="test")], engine="codex")

    await _send_runner_unavailable(
        exec_cfg,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        resume_token=None,
        runner=runner,
        reason="Engine is down for maintenance",
    )

    assert len(transport.send_calls) == 1
    call = transport.send_calls[0]
    assert call["channel_id"] == MATRIX_ROOM_ID
    assert "error" in call["message"].text.lower()


@pytest.mark.anyio
async def test_send_runner_unavailable_with_resume_token() -> None:
    """_send_runner_unavailable includes resume token in state."""
    from takopi.api import ResumeToken
    from takopi_matrix.bridge.events import _send_runner_unavailable

    transport = FakeTransport()
    exec_cfg = _make_exec_cfg(transport)
    runner = ScriptRunner([Return(answer="test")], engine="codex")
    resume = ResumeToken(value="resume-123", engine="codex")

    await _send_runner_unavailable(
        exec_cfg,
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        resume_token=resume,
        runner=runner,
        reason="Rate limited",
    )

    assert len(transport.send_calls) == 1
    call = transport.send_calls[0]
    assert call["options"].reply_to.message_id == MATRIX_EVENT_ID


# --- _send_with_resume tests ---


@pytest.mark.anyio
async def test_send_with_resume_no_resume_ready() -> None:
    """_send_with_resume sends error if no resume token available."""
    from takopi_matrix.bridge.events import _send_with_resume

    transport = FakeTransport()
    exec_cfg_instance = _make_exec_cfg(transport)

    # Create a minimal config with just exec_cfg
    class MinimalConfig:
        pass

    config = MinimalConfig()
    config.exec_cfg = exec_cfg_instance  # type: ignore

    # Create a running task that completes before resume is set
    running_task = RunningTask()

    async def complete_task():
        await anyio.sleep(0.01)
        running_task.done.set()

    enqueue_calls: list[tuple] = []

    async def enqueue(room_id, event_id, text, resume, context):
        enqueue_calls.append((room_id, event_id, text, resume, context))

    async with anyio.create_task_group() as tg:
        tg.start_soon(complete_task)
        await _send_with_resume(
            config,  # type: ignore
            enqueue,
            running_task,
            MATRIX_ROOM_ID,
            MATRIX_EVENT_ID,
            "test message",
        )

    # Should have sent error message, not enqueued
    assert len(transport.send_calls) == 1
    assert "resume token not ready" in transport.send_calls[0]["message"].text
    assert len(enqueue_calls) == 0


@pytest.mark.anyio
async def test_send_with_resume_with_token() -> None:
    """_send_with_resume enqueues if resume token available."""
    from takopi.api import ResumeToken
    from takopi_matrix.bridge.events import _send_with_resume

    transport = FakeTransport()
    exec_cfg_instance = _make_exec_cfg(transport)

    class MinimalConfig:
        pass

    config = MinimalConfig()
    config.exec_cfg = exec_cfg_instance  # type: ignore

    # Create a running task with resume already set
    running_task = RunningTask()
    running_task.resume = ResumeToken(value="token-456", engine="codex")

    enqueue_calls: list[tuple] = []

    async def enqueue(room_id, event_id, text, resume, context):
        enqueue_calls.append((room_id, event_id, text, resume, context))

    await _send_with_resume(
        config,  # type: ignore
        enqueue,
        running_task,
        MATRIX_ROOM_ID,
        MATRIX_EVENT_ID,
        "test message",
    )

    # Should have enqueued, not sent error
    assert len(transport.send_calls) == 0
    assert len(enqueue_calls) == 1
    assert enqueue_calls[0][0] == MATRIX_ROOM_ID
    assert enqueue_calls[0][2] == "test message"


# --- _enrich_with_reply_text tests ---


@pytest.mark.anyio
async def test_enrich_with_reply_text_no_reply() -> None:
    """_enrich_with_reply_text returns message unchanged if no reply."""
    from takopi_matrix.bridge.events import _enrich_with_reply_text
    from takopi_matrix.types import MatrixIncomingMessage

    class FakeClient:
        async def get_event_text(self, room_id, event_id):
            return "original text"

    class FakeConfig:
        client = FakeClient()

    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender="@user:example.org",
        text="Hello",
        reply_to_event_id=None,
    )

    result = await _enrich_with_reply_text(FakeConfig(), msg)  # type: ignore

    assert result == msg
    assert result.reply_to_text is None


@pytest.mark.anyio
async def test_enrich_with_reply_text_with_reply() -> None:
    """_enrich_with_reply_text fetches and adds reply text."""
    from takopi_matrix.bridge.events import _enrich_with_reply_text
    from takopi_matrix.types import MatrixIncomingMessage

    class FakeClient:
        async def get_event_text(self, room_id, event_id):
            return "This is the original message"

    class FakeConfig:
        client = FakeClient()

    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender="@user:example.org",
        text="Reply text",
        reply_to_event_id="$original123",
    )

    result = await _enrich_with_reply_text(FakeConfig(), msg)  # type: ignore

    assert result.reply_to_text == "This is the original message"
    assert result.text == "Reply text"  # Original text unchanged


@pytest.mark.anyio
async def test_enrich_with_reply_text_fetch_failed() -> None:
    """_enrich_with_reply_text returns original if fetch fails."""
    from takopi_matrix.bridge.events import _enrich_with_reply_text
    from takopi_matrix.types import MatrixIncomingMessage

    class FakeClient:
        async def get_event_text(self, room_id, event_id):
            return None  # Fetch failed

    class FakeConfig:
        client = FakeClient()

    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id=MATRIX_ROOM_ID,
        event_id=MATRIX_EVENT_ID,
        sender="@user:example.org",
        text="Reply text",
        reply_to_event_id="$missing123",
    )

    result = await _enrich_with_reply_text(FakeConfig(), msg)  # type: ignore

    assert result == msg
    assert result.reply_to_text is None


# --- _process_single_event tests ---
# Note: Class names must match what _process_single_event checks via type().__name__


class RoomEncrypted:
    """Fake encrypted event for testing - named to match nio class."""

    def __init__(self, sender: str = "@other:example.org"):
        self.sender = sender
        self.event_id = "$encrypted123"


class MegolmEvent:
    """Fake Megolm encrypted event - named to match nio class."""

    def __init__(self, sender: str = "@other:example.org"):
        self.sender = sender
        self.event_id = "$megolm123"


class RoomMessageText:
    """Fake text message event - named to match nio class."""

    def __init__(
        self,
        sender: str = "@other:example.org",
        body: str = "Hello",
        event_id: str = "$msg123",
    ):
        self.sender = sender
        self.event_id = event_id
        self.body = body
        self.formatted_body = None
        self.source = {
            "content": {
                "m.relates_to": None,
            }
        }


class RoomMessageImage:
    """Fake image message event - named to match nio class."""

    def __init__(self, sender: str = "@other:example.org"):
        self.sender = sender
        self.event_id = "$img123"
        self.body = "image.png"
        self.url = "mxc://example.org/image123"
        self.source = {
            "content": {
                "info": {"size": 1024, "mimetype": "image/png"},
            }
        }


class RoomMessageAudio:
    """Fake audio message event - named to match nio class."""

    def __init__(self, sender: str = "@other:example.org"):
        self.sender = sender
        self.event_id = "$audio123"
        self.body = "audio.ogg"
        self.url = "mxc://example.org/audio123"
        self.source = {
            "content": {
                "info": {
                    "size": 2048,
                    "mimetype": "audio/ogg",
                    "duration": 5000,
                },
            }
        }


class ReactionEvent:
    """Fake reaction event - named to match nio class."""

    def __init__(
        self,
        sender: str = "@other:example.org",
        relates_to_event: str = "$original",
        key: str = "👍",
    ):
        self.sender = sender
        self.event_id = "$reaction123"
        self.source = {
            "content": {
                "m.relates_to": {
                    "event_id": relates_to_event,
                    "key": key,
                    "rel_type": "m.annotation",
                }
            }
        }


class FakeClientForEvents:
    """Fake MatrixClient for event processing tests."""

    def __init__(self):
        self.e2ee_available = True
        self._decrypt_result: object | None = None
        self.decrypt_calls: list[object] = []
        self.join_calls: list[str] = []
        self.trust_calls: list[str] = []
        self._join_result = True

    async def decrypt_event(self, event):
        self.decrypt_calls.append(event)
        return self._decrypt_result

    async def join_room(self, room_id: str) -> bool:
        self.join_calls.append(room_id)
        return self._join_result

    async def trust_room_devices(self, room_id: str) -> None:
        self.trust_calls.append(room_id)


class FakeMatrixBridgeConfig:
    """Fake MatrixBridgeConfig for event tests."""

    def __init__(self, client: FakeClientForEvents | None = None):
        self.client = client or FakeClientForEvents()
        self.user_allowlist: set[str] | None = None


@pytest.fixture
def event_queues():
    """Create message and reaction queues for testing."""
    msg_send, msg_recv = anyio.create_memory_object_stream(10)
    react_send, react_recv = anyio.create_memory_object_stream(10)
    return msg_send, msg_recv, react_send, react_recv


@pytest.mark.anyio
async def test_process_single_event_encrypted_own_message(event_queues) -> None:
    """Encrypted events from own user are skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()
    own_user = "@bot:example.org"

    event = RoomEncrypted(sender=own_user)

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=own_user,
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Should not queue anything - own message ignored
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_encrypted_decryption_failed(event_queues) -> None:
    """Encrypted event with failed decryption is logged and skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    client._decrypt_result = None  # Decryption fails
    cfg = FakeMatrixBridgeConfig(client)

    event = RoomEncrypted(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Decryption was attempted
    assert len(client.decrypt_calls) == 1
    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_encrypted_e2ee_not_available(event_queues) -> None:
    """Encrypted event when E2EE not available is logged and skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    client.e2ee_available = False
    cfg = FakeMatrixBridgeConfig(client)

    event = RoomEncrypted(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # No decryption attempted
    assert len(client.decrypt_calls) == 0
    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_encrypted_decrypted_to_text(event_queues) -> None:
    """Successfully decrypted event is processed as text message."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    # Decrypt returns a text message event
    client._decrypt_result = RoomMessageText(
        sender="@other:example.org", body="Decrypted hello"
    )
    cfg = FakeMatrixBridgeConfig(client)

    event = RoomEncrypted(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Message should be queued
    assert len(client.decrypt_calls) == 1
    msg = msg_recv.receive_nowait()
    assert msg.text == "Decrypted hello"


@pytest.mark.anyio
async def test_process_single_event_text_room_not_allowed(event_queues) -> None:
    """Text message from non-allowed room is skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    event = RoomMessageText(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        "!other:example.org",  # Different room
        allowed_room_ids={MATRIX_ROOM_ID},  # Only allows one room
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_text_own_message(event_queues) -> None:
    """Text message from own user is skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()
    own_user = "@bot:example.org"

    event = RoomMessageText(sender=own_user)

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id=own_user,
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_text_sender_not_in_allowlist(event_queues) -> None:
    """Text message from sender not in allowlist is skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()
    cfg.user_allowlist = {"@allowed:example.org"}

    event = RoomMessageText(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_text_success(event_queues) -> None:
    """Text message from allowed user in allowed room is processed."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    event = RoomMessageText(sender="@user:example.org", body="Hello, bot!")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Message should be queued
    msg = msg_recv.receive_nowait()
    assert msg.text == "Hello, bot!"
    assert msg.sender == "@user:example.org"


@pytest.mark.anyio
async def test_process_single_event_media_sender_not_allowed(event_queues) -> None:
    """Media message from sender not in allowlist is skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()
    cfg.user_allowlist = {"@allowed:example.org"}

    event = RoomMessageImage(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_media_success(event_queues) -> None:
    """Media message from allowed user is processed."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    event = RoomMessageImage(sender="@user:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Message should be queued
    msg = msg_recv.receive_nowait()
    assert msg.attachments is not None
    assert len(msg.attachments) > 0
    assert msg.attachments[0].mxc_url == "mxc://example.org/image123"


@pytest.mark.anyio
async def test_process_single_event_audio_sender_not_allowed(event_queues) -> None:
    """Audio message from sender not in allowlist is skipped."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()
    cfg.user_allowlist = {"@allowed:example.org"}

    event = RoomMessageAudio(sender="@other:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # No message queued
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_single_event_audio_success(event_queues) -> None:
    """Audio message from allowed user is processed."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    event = RoomMessageAudio(sender="@user:example.org")

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Message should be queued
    msg = msg_recv.receive_nowait()
    assert msg.voice is not None
    assert msg.voice.mxc_url == "mxc://example.org/audio123"


@pytest.mark.anyio
async def test_process_single_event_reaction(event_queues) -> None:
    """Reaction event is processed."""
    from takopi_matrix.bridge.events import _process_single_event

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    event = ReactionEvent(
        sender="@user:example.org", relates_to_event="$original", key="👍"
    )

    await _process_single_event(
        cfg,  # type: ignore
        event,
        MATRIX_ROOM_ID,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Reaction should be queued
    reaction = react_recv.receive_nowait()
    assert reaction.target_event_id == "$original"
    assert reaction.key == "👍"


# --- _process_room_timeline tests ---


class FakeTimeline:
    """Fake room timeline."""

    def __init__(self, events: list):
        self.events = events


class FakeRoomInfo:
    """Fake room info with timeline."""

    def __init__(self, events: list | None = None):
        if events is not None:
            self.timeline = FakeTimeline(events)
        else:
            self.timeline = None


@pytest.mark.anyio
async def test_process_room_timeline_no_timeline(event_queues) -> None:
    """Room with no timeline is skipped."""
    from takopi_matrix.bridge.events import _process_room_timeline

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    room_info = FakeRoomInfo(events=None)

    await _process_room_timeline(
        cfg,  # type: ignore
        MATRIX_ROOM_ID,
        room_info,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Nothing should happen
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_room_timeline_with_events(event_queues) -> None:
    """Room timeline with events processes all events."""
    from takopi_matrix.bridge.events import _process_room_timeline

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    cfg = FakeMatrixBridgeConfig(client)

    events = [
        RoomMessageText(sender="@user:example.org", body="Message 1"),
        RoomMessageText(sender="@user:example.org", body="Message 2"),
    ]
    room_info = FakeRoomInfo(events=events)

    await _process_room_timeline(
        cfg,  # type: ignore
        MATRIX_ROOM_ID,
        room_info,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Both messages should be queued
    msg1 = msg_recv.receive_nowait()
    assert msg1.text == "Message 1"
    msg2 = msg_recv.receive_nowait()
    assert msg2.text == "Message 2"

    # Trust should have been called for allowed room
    assert MATRIX_ROOM_ID in client.trust_calls


@pytest.mark.anyio
async def test_process_room_timeline_trusts_devices(event_queues) -> None:
    """Room timeline with e2ee available trusts devices in allowed rooms."""
    from takopi_matrix.bridge.events import _process_room_timeline

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    cfg = FakeMatrixBridgeConfig(client)

    room_info = FakeRoomInfo(events=[])

    await _process_room_timeline(
        cfg,  # type: ignore
        MATRIX_ROOM_ID,
        room_info,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Trust should be called for allowed room
    assert MATRIX_ROOM_ID in client.trust_calls


@pytest.mark.anyio
async def test_process_room_timeline_no_trust_for_other_rooms(event_queues) -> None:
    """Room timeline does not trust devices for non-allowed rooms."""
    from takopi_matrix.bridge.events import _process_room_timeline

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    cfg = FakeMatrixBridgeConfig(client)

    room_info = FakeRoomInfo(events=[])

    await _process_room_timeline(
        cfg,  # type: ignore
        "!other:example.org",
        room_info,
        allowed_room_ids={MATRIX_ROOM_ID},  # Different room
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Trust should NOT be called for non-allowed room
    assert len(client.trust_calls) == 0


# --- _process_invite_events tests ---


class FakeInviteState:
    """Fake invite state event."""

    def __init__(self, sender: str, name: str | None = None):
        self.sender = sender
        if name:
            self.name = name


class FakeInviteInfo:
    """Fake invite info."""

    def __init__(self, sender: str, name: str | None = None):
        self.invite_state = [FakeInviteState(sender, name)]


class FakeRooms:
    """Fake rooms object in sync response."""

    def __init__(
        self,
        join: dict | None = None,
        invite: dict | None = None,
    ):
        self.join = join or {}
        self.invite = invite or {}


class FakeSyncResponse:
    """Fake sync response."""

    def __init__(self, rooms: FakeRooms | None = None):
        self.rooms = rooms


@pytest.mark.anyio
async def test_process_invite_events_no_rooms() -> None:
    """Sync response with no rooms returns empty list."""
    from takopi_matrix.bridge.events import _process_invite_events

    cfg = FakeMatrixBridgeConfig()
    response = FakeSyncResponse(rooms=None)
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == []


@pytest.mark.anyio
async def test_process_invite_events_no_invites() -> None:
    """Sync response with no invites returns empty list."""
    from takopi_matrix.bridge.events import _process_invite_events

    cfg = FakeMatrixBridgeConfig()
    response = FakeSyncResponse(rooms=FakeRooms(invite={}))
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == []


@pytest.mark.anyio
async def test_process_invite_events_inviter_not_allowed() -> None:
    """Invite from non-allowed user is not joined."""
    from takopi_matrix.bridge.events import _process_invite_events

    client = FakeClientForEvents()
    cfg = FakeMatrixBridgeConfig(client)
    cfg.user_allowlist = {"@allowed:example.org"}

    response = FakeSyncResponse(
        rooms=FakeRooms(
            invite={"!newroom:example.org": FakeInviteInfo("@other:example.org")}
        )
    )
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == []
    assert len(client.join_calls) == 0


@pytest.mark.anyio
async def test_process_invite_events_auto_join_success() -> None:
    """Invite from allowed user triggers auto-join."""
    from takopi_matrix.bridge.events import _process_invite_events

    client = FakeClientForEvents()
    client._join_result = True
    cfg = FakeMatrixBridgeConfig(client)
    cfg.user_allowlist = {"@allowed:example.org"}

    response = FakeSyncResponse(
        rooms=FakeRooms(
            invite={
                "!newroom:example.org": FakeInviteInfo(
                    "@allowed:example.org", name="New Room"
                )
            }
        )
    )
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == ["!newroom:example.org"]
    assert "!newroom:example.org" in client.join_calls
    assert "!newroom:example.org" in allowed


@pytest.mark.anyio
async def test_process_invite_events_auto_join_no_allowlist() -> None:
    """Invite triggers auto-join when no allowlist configured."""
    from takopi_matrix.bridge.events import _process_invite_events

    client = FakeClientForEvents()
    client._join_result = True
    cfg = FakeMatrixBridgeConfig(client)
    cfg.user_allowlist = None  # No allowlist

    response = FakeSyncResponse(
        rooms=FakeRooms(
            invite={"!newroom:example.org": FakeInviteInfo("@anyone:example.org")}
        )
    )
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == ["!newroom:example.org"]
    assert "!newroom:example.org" in client.join_calls


@pytest.mark.anyio
async def test_process_invite_events_join_failed() -> None:
    """Failed join does not add room to allowed list."""
    from takopi_matrix.bridge.events import _process_invite_events

    client = FakeClientForEvents()
    client._join_result = False  # Join fails
    cfg = FakeMatrixBridgeConfig(client)

    response = FakeSyncResponse(
        rooms=FakeRooms(
            invite={"!newroom:example.org": FakeInviteInfo("@user:example.org")}
        )
    )
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == []
    assert "!newroom:example.org" in client.join_calls
    assert "!newroom:example.org" not in allowed


@pytest.mark.anyio
async def test_process_invite_events_join_error() -> None:
    """Join error does not add room to allowed list."""
    from takopi_matrix.bridge.events import _process_invite_events

    client = FakeClientForEvents()

    # Override join_room to raise
    async def raise_on_join(room_id):
        raise Exception("Network error")

    client.join_room = raise_on_join  # type: ignore
    cfg = FakeMatrixBridgeConfig(client)

    response = FakeSyncResponse(
        rooms=FakeRooms(
            invite={"!newroom:example.org": FakeInviteInfo("@user:example.org")}
        )
    )
    allowed = {MATRIX_ROOM_ID}

    result = await _process_invite_events(
        cfg,  # type: ignore
        response,
        allowed_room_ids=allowed,
    )

    assert result == []
    assert "!newroom:example.org" not in allowed


# --- _process_sync_response tests ---


@pytest.mark.anyio
async def test_process_sync_response_no_rooms(event_queues) -> None:
    """Sync response with no rooms does nothing."""
    from takopi_matrix.bridge.events import _process_sync_response

    msg_send, msg_recv, react_send, react_recv = event_queues
    cfg = FakeMatrixBridgeConfig()

    response = FakeSyncResponse(rooms=None)

    await _process_sync_response(
        cfg,  # type: ignore
        response,
        allowed_room_ids={MATRIX_ROOM_ID},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Nothing should happen
    with anyio.fail_after(0.1):
        try:
            msg_recv.receive_nowait()
            pytest.fail("Should not have received a message")
        except anyio.WouldBlock:
            pass


@pytest.mark.anyio
async def test_process_sync_response_processes_joined_rooms(event_queues) -> None:
    """Sync response processes all joined rooms."""
    from takopi_matrix.bridge.events import _process_sync_response

    msg_send, msg_recv, react_send, react_recv = event_queues
    client = FakeClientForEvents()
    cfg = FakeMatrixBridgeConfig(client)

    room1_info = FakeRoomInfo(
        events=[RoomMessageText(sender="@user:example.org", body="Room 1 msg")]
    )
    room2_info = FakeRoomInfo(
        events=[RoomMessageText(sender="@user:example.org", body="Room 2 msg")]
    )

    response = FakeSyncResponse(
        rooms=FakeRooms(
            join={
                MATRIX_ROOM_ID: room1_info,
                "!room2:example.org": room2_info,
            }
        )
    )

    await _process_sync_response(
        cfg,  # type: ignore
        response,
        allowed_room_ids={MATRIX_ROOM_ID, "!room2:example.org"},
        own_user_id="@bot:example.org",
        message_queue=msg_send,
        reaction_queue=react_send,
    )

    # Both messages should be queued
    messages = []
    try:
        while True:
            messages.append(msg_recv.receive_nowait())
    except anyio.WouldBlock:
        pass

    assert len(messages) == 2
    texts = {m.text for m in messages}
    assert "Room 1 msg" in texts
    assert "Room 2 msg" in texts
