"""Tests for engine resolution logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from takopi_matrix.engine_defaults import (
    EngineResolution,
    _allowed_room_ids,
    resolve_engine_for_message,
)
from takopi_matrix.room_prefs import RoomPrefsStore


@pytest.fixture
def room_prefs(tmp_path: Path) -> RoomPrefsStore:
    """Create a room prefs store with temporary storage."""
    return RoomPrefsStore(tmp_path / "matrix_room_prefs_state.json")


@pytest.fixture
def mock_runtime() -> MagicMock:
    """Create a mock TransportRuntime."""
    runtime = MagicMock()
    runtime.default_engine = "haiku"
    runtime.project_default_engine.return_value = None
    return runtime


class TestEngineResolution:
    def test_dataclass_fields(self) -> None:
        res = EngineResolution(
            engine="opus",
            source="directive",
            room_default="sonnet",
            project_default="haiku",
        )
        assert res.engine == "opus"
        assert res.source == "directive"
        assert res.room_default == "sonnet"
        assert res.project_default == "haiku"

    def test_frozen(self) -> None:
        res = EngineResolution(
            engine="opus",
            source="directive",
            room_default=None,
            project_default=None,
        )
        with pytest.raises(AttributeError):
            res.engine = "sonnet"  # type: ignore[misc]


class TestResolveEngineForMessage:
    @pytest.mark.anyio
    async def test_directive_takes_priority(
        self,
        mock_runtime: MagicMock,
        room_prefs: RoomPrefsStore,
    ) -> None:
        """Explicit directive overrides all other sources."""
        await room_prefs.set_default_engine("!room:example.org", "sonnet")
        mock_runtime.project_default_engine.return_value = "haiku"

        result = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine="opus",
            room_id="!room:example.org",
            room_prefs=room_prefs,
        )

        assert result.engine == "opus"
        assert result.source == "directive"
        assert result.room_default == "sonnet"
        assert result.project_default == "haiku"

    @pytest.mark.anyio
    async def test_room_default_second_priority(
        self,
        mock_runtime: MagicMock,
        room_prefs: RoomPrefsStore,
    ) -> None:
        """Room default used when no directive."""
        await room_prefs.set_default_engine("!room:example.org", "sonnet")
        mock_runtime.project_default_engine.return_value = "haiku"

        result = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine=None,
            room_id="!room:example.org",
            room_prefs=room_prefs,
        )

        assert result.engine == "sonnet"
        assert result.source == "room_default"

    @pytest.mark.anyio
    async def test_project_default_third_priority(
        self,
        mock_runtime: MagicMock,
        room_prefs: RoomPrefsStore,
    ) -> None:
        """Project default used when no directive or room default."""
        mock_runtime.project_default_engine.return_value = "opus"

        result = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine=None,
            room_id="!room:example.org",
            room_prefs=room_prefs,
        )

        assert result.engine == "opus"
        assert result.source == "project_default"

    @pytest.mark.anyio
    async def test_global_default_fallback(
        self,
        mock_runtime: MagicMock,
        room_prefs: RoomPrefsStore,
    ) -> None:
        """Global default used as fallback."""
        mock_runtime.default_engine = "haiku"

        result = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine=None,
            room_id="!room:example.org",
            room_prefs=room_prefs,
        )

        assert result.engine == "haiku"
        assert result.source == "global_default"

    @pytest.mark.anyio
    async def test_no_room_prefs_store(
        self,
        mock_runtime: MagicMock,
    ) -> None:
        """Works without room prefs store."""
        result = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine=None,
            room_id="!room:example.org",
            room_prefs=None,
        )

        assert result.engine == mock_runtime.default_engine
        assert result.source == "global_default"
        assert result.room_default is None

    @pytest.mark.anyio
    async def test_different_rooms_different_engines(
        self,
        mock_runtime: MagicMock,
        room_prefs: RoomPrefsStore,
    ) -> None:
        """Different rooms can have different engines."""
        await room_prefs.set_default_engine("!room1:example.org", "opus")
        await room_prefs.set_default_engine("!room2:example.org", "sonnet")

        result1 = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine=None,
            room_id="!room1:example.org",
            room_prefs=room_prefs,
        )

        result2 = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,
            explicit_engine=None,
            room_id="!room2:example.org",
            room_prefs=room_prefs,
        )

        assert result1.engine == "opus"
        assert result2.engine == "sonnet"


class TestAllowedRoomIds:
    def test_configured_rooms_included(self) -> None:
        runtime = MagicMock()
        result = _allowed_room_ids(
            ["!room1:example.org", "!room2:example.org"],
            runtime,
        )
        assert result == {"!room1:example.org", "!room2:example.org"}

    def test_empty_list(self) -> None:
        runtime = MagicMock()
        result = _allowed_room_ids([], runtime)
        assert result == set()

    def test_deduplicates(self) -> None:
        runtime = MagicMock()
        result = _allowed_room_ids(
            ["!room:example.org", "!room:example.org"],
            runtime,
        )
        assert result == {"!room:example.org"}
