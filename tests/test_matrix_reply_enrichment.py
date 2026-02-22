"""Tests for Matrix reply enrichment status handling."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from takopi_matrix.bridge.config import MatrixBridgeConfig
from takopi_matrix.bridge.events import _enrich_with_reply_text
from takopi_matrix.types import MatrixIncomingMessage


def _message(*, reply_to_event_id: str = "$reply:example.org") -> MatrixIncomingMessage:
    return MatrixIncomingMessage(
        transport="matrix",
        room_id="!room:example.org",
        event_id="$msg:example.org",
        sender="@user:example.org",
        text="hello",
        reply_to_event_id=reply_to_event_id,
    )


@pytest.mark.anyio
async def test_reply_enrichment_marks_fetch_failed_on_decrypt_error() -> None:
    client = SimpleNamespace(
        get_event_text=AsyncMock(
            return_value=SimpleNamespace(text=None, status="decrypt_failed")
        )
    )
    cfg = cast(MatrixBridgeConfig, SimpleNamespace(client=client))

    msg = await _enrich_with_reply_text(cfg, _message())

    assert msg.reply_to_text is None
    assert msg.reply_to_text_fetch_failed is True


@pytest.mark.anyio
async def test_reply_enrichment_does_not_mark_missing_body_as_fetch_failed() -> None:
    client = SimpleNamespace(
        get_event_text=AsyncMock(
            return_value=SimpleNamespace(text=None, status="missing")
        )
    )
    cfg = cast(MatrixBridgeConfig, SimpleNamespace(client=client))

    msg = await _enrich_with_reply_text(cfg, _message())

    assert msg.reply_to_text is None
    assert msg.reply_to_text_fetch_failed is False


@pytest.mark.anyio
async def test_reply_enrichment_sets_text_on_success() -> None:
    client = SimpleNamespace(
        get_event_text=AsyncMock(
            return_value=SimpleNamespace(text="codex resume abc", status="ok")
        )
    )
    cfg = cast(MatrixBridgeConfig, SimpleNamespace(client=client))

    msg = await _enrich_with_reply_text(cfg, _message())

    assert msg.reply_to_text == "codex resume abc"
    assert msg.reply_to_text_fetch_failed is False
