"""Tests for Matrix chat/thread session stores."""

from __future__ import annotations

from pathlib import Path

import pytest

from takopi.api import ResumeToken, RunContext
from takopi_matrix.chat_sessions import (
    MatrixChatSessionStore,
    resolve_chat_sessions_path,
)
from takopi_matrix.thread_state import MatrixThreadStateStore, resolve_thread_state_path


class TestChatSessionsPath:
    def test_resolve_path_adjacent_to_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "takopi.toml"
        result = resolve_chat_sessions_path(config_path)
        assert result == tmp_path / "matrix_chat_sessions_state.json"


class TestThreadStatePath:
    def test_resolve_path_adjacent_to_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "takopi.toml"
        result = resolve_thread_state_path(config_path)
        assert result == tmp_path / "matrix_thread_state.json"


@pytest.mark.anyio
async def test_chat_sessions_set_get_and_clear(tmp_path: Path) -> None:
    store = MatrixChatSessionStore(tmp_path / "matrix_chat_sessions_state.json")
    room_id = "!room:example.org"
    sender = "@user:example.org"

    token = ResumeToken(engine="codex", value="resume-1")
    await store.set_session_resume(room_id, sender, token)
    loaded = await store.get_session_resume(room_id, sender, "codex")
    assert loaded is not None
    assert loaded.engine == "codex"
    assert loaded.value == "resume-1"

    await store.clear_sessions(room_id, sender)
    cleared = await store.get_session_resume(room_id, sender, "codex")
    assert cleared is None


@pytest.mark.anyio
async def test_chat_sessions_are_per_sender(tmp_path: Path) -> None:
    store = MatrixChatSessionStore(tmp_path / "matrix_chat_sessions_state.json")
    room_id = "!room:example.org"
    await store.set_session_resume(
        room_id, "@alice:example.org", ResumeToken(engine="codex", value="a")
    )
    await store.set_session_resume(
        room_id, "@bob:example.org", ResumeToken(engine="codex", value="b")
    )

    alice = await store.get_session_resume(room_id, "@alice:example.org", "codex")
    bob = await store.get_session_resume(room_id, "@bob:example.org", "codex")
    assert alice is not None and alice.value == "a"
    assert bob is not None and bob.value == "b"


@pytest.mark.anyio
async def test_chat_sessions_sync_startup_cwd_clears_on_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "matrix_chat_sessions_state.json"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    store = MatrixChatSessionStore(path)
    assert await store.sync_startup_cwd(Path.cwd()) is False
    await store.set_session_resume(
        "!room:example.org",
        "@user:example.org",
        ResumeToken(engine="codex", value="resume-1"),
    )

    store_same_cwd = MatrixChatSessionStore(path)
    assert await store_same_cwd.sync_startup_cwd(Path.cwd()) is False
    assert (
        await store_same_cwd.get_session_resume(
            "!room:example.org", "@user:example.org", "codex"
        )
    ) is not None

    monkeypatch.chdir(second_cwd)
    store_changed = MatrixChatSessionStore(path)
    assert await store_changed.sync_startup_cwd(Path.cwd()) is True
    assert (
        await store_changed.get_session_resume(
            "!room:example.org", "@user:example.org", "codex"
        )
    ) is None


@pytest.mark.anyio
async def test_thread_sessions_set_get_and_clear(tmp_path: Path) -> None:
    store = MatrixThreadStateStore(tmp_path / "matrix_thread_state.json")
    room_id = "!room:example.org"
    thread_root = "$threadroot:example.org"

    token = ResumeToken(engine="codex", value="thread-resume")
    await store.set_session_resume(room_id, thread_root, token)
    loaded = await store.get_session_resume(room_id, thread_root, "codex")
    assert loaded is not None
    assert loaded.engine == "codex"
    assert loaded.value == "thread-resume"

    await store.clear_sessions(room_id, thread_root)
    cleared = await store.get_session_resume(room_id, thread_root, "codex")
    assert cleared is None


@pytest.mark.anyio
async def test_thread_sessions_are_per_thread_root(tmp_path: Path) -> None:
    store = MatrixThreadStateStore(tmp_path / "matrix_thread_state.json")
    room_id = "!room:example.org"
    await store.set_session_resume(
        room_id, "$thread-a:example.org", ResumeToken(engine="codex", value="a")
    )
    await store.set_session_resume(
        room_id, "$thread-b:example.org", ResumeToken(engine="codex", value="b")
    )

    a = await store.get_session_resume(room_id, "$thread-a:example.org", "codex")
    b = await store.get_session_resume(room_id, "$thread-b:example.org", "codex")
    assert a is not None and a.value == "a"
    assert b is not None and b.value == "b"


@pytest.mark.anyio
async def test_thread_context_set_get_and_clear(tmp_path: Path) -> None:
    store = MatrixThreadStateStore(tmp_path / "matrix_thread_state.json")
    room_id = "!room:example.org"
    thread_root = "$thread-a:example.org"

    await store.set_context(
        room_id, thread_root, RunContext(project="iphone-app", branch="feat/ui")
    )
    context = await store.get_context(room_id, thread_root)
    assert context is not None
    assert context.project == "iphone-app"
    assert context.branch == "feat/ui"

    await store.clear_context(room_id, thread_root)
    assert await store.get_context(room_id, thread_root) is None
