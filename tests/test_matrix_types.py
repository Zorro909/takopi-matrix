"""Tests for Matrix transport type definitions."""

from __future__ import annotations

import pytest

from takopi_matrix.types import (
    MatrixFile,
    MatrixIncomingMessage,
    MatrixReaction,
    MatrixVoice,
)
from matrix_fixtures import (
    make_matrix_file,
    make_matrix_message,
    make_matrix_reaction,
    make_matrix_voice,
)


def test_matrix_file_creation() -> None:
    """MatrixFile can be created with all fields."""
    f = MatrixFile(
        mxc_url="mxc://example.org/abc123",
        filename="test.png",
        mimetype="image/png",
        size=1024,
    )
    assert f.mxc_url == "mxc://example.org/abc123"
    assert f.filename == "test.png"
    assert f.mimetype == "image/png"
    assert f.size == 1024


def test_matrix_file_optional_fields() -> None:
    """MatrixFile size is optional."""
    f = MatrixFile(
        mxc_url="mxc://example.org/abc",
        filename="doc.pdf",
        mimetype="application/pdf",
    )
    assert f.size is None


def test_matrix_voice_creation() -> None:
    """MatrixVoice can be created with duration."""
    v = MatrixVoice(
        mxc_url="mxc://example.org/voice",
        mimetype="audio/ogg",
        size=5000,
        duration_ms=3000,
        raw={"content": {}},
    )
    assert v.mxc_url == "mxc://example.org/voice"
    assert v.duration_ms == 3000


def test_matrix_voice_optional_duration() -> None:
    """MatrixVoice duration is optional."""
    v = MatrixVoice(
        mxc_url="mxc://example.org/voice",
        mimetype="audio/ogg",
        size=5000,
        duration_ms=None,
        raw={"content": {}},
    )
    assert v.duration_ms is None


def test_matrix_incoming_message_minimal() -> None:
    """MatrixIncomingMessage with only required fields."""
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$evt:example.org",
        sender="@user:example.org",
        text="hello",
    )
    assert msg.transport == "matrix"
    assert msg.room_id == "!room:example.org"
    assert msg.text == "hello"
    assert msg.reply_to_event_id is None
    assert msg.attachments is None
    assert msg.voice is None


def test_matrix_incoming_message_full() -> None:
    """MatrixIncomingMessage with all optional fields."""
    file = make_matrix_file()
    voice = make_matrix_voice()
    msg = MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$evt:example.org",
        sender="@user:example.org",
        text="hello",
        reply_to_event_id="$prev:example.org",
        reply_to_text="previous message",
        formatted_body="<p>hello</p>",
        attachments=[file],
        voice=voice,
        raw={"source": "test"},
    )
    assert msg.reply_to_event_id == "$prev:example.org"
    assert msg.reply_to_text == "previous message"
    assert msg.formatted_body == "<p>hello</p>"
    assert msg.attachments == [file]
    assert msg.voice == voice
    assert msg.raw == {"source": "test"}


def test_matrix_reaction_creation() -> None:
    """MatrixReaction can be created."""
    r = MatrixReaction(
        room_id="!room:example.org",
        event_id="$reaction:example.org",
        target_event_id="$target:example.org",
        sender="@user:example.org",
        key="👍",
    )
    assert r.room_id == "!room:example.org"
    assert r.target_event_id == "$target:example.org"
    assert r.key == "👍"


def test_matrix_reaction_cancel_emoji() -> None:
    """MatrixReaction can hold cancel emoji."""
    r = make_matrix_reaction(key="❌")
    assert r.key == "❌"


def test_dataclass_frozen() -> None:
    """Matrix dataclasses are frozen (immutable)."""
    msg = make_matrix_message()
    with pytest.raises(AttributeError):
        msg.text = "modified"  # type: ignore[misc]

    f = make_matrix_file()
    with pytest.raises(AttributeError):
        f.filename = "modified.txt"  # type: ignore[misc]

    r = make_matrix_reaction()
    with pytest.raises(AttributeError):
        r.key = "😀"  # type: ignore[misc]


def test_fixture_helpers() -> None:
    """Fixture helper functions create valid objects."""
    msg = make_matrix_message(text="custom text")
    assert msg.text == "custom text"
    assert msg.transport == "matrix"

    f = make_matrix_file(filename="custom.txt")
    assert f.filename == "custom.txt"

    v = make_matrix_voice(duration_ms=5000)
    assert v.duration_ms == 5000

    r = make_matrix_reaction(key="🎉")
    assert r.key == "🎉"
