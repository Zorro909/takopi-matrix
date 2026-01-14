"""Tests for room-to-project mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from takopi.api import RunContext

from takopi_matrix.room_projects import (
    RoomProjectMap,
    build_room_project_map,
)


@pytest.fixture
def mock_runtime() -> MagicMock:
    """Create a mock TransportRuntime with project info."""
    runtime = MagicMock()
    runtime.project_aliases.return_value = ["myproject", "other"]
    runtime.normalize_project_key.side_effect = lambda key: (
        key.lower() if key.lower() in ("myproject", "other") else None
    )
    return runtime


class TestRoomProjectMap:
    def test_empty_mapping(self, mock_runtime: MagicMock) -> None:
        """Empty config produces empty mapping."""
        room_map = RoomProjectMap({}, mock_runtime)
        assert room_map.project_for_room("!room:example.org") is None
        assert room_map.project_room_ids() == ()

    def test_valid_mapping(self, mock_runtime: MagicMock) -> None:
        """Valid room-project mapping works."""
        room_map = RoomProjectMap(
            {"!room:example.org": "myproject"},
            mock_runtime,
        )
        assert room_map.project_for_room("!room:example.org") == "myproject"

    def test_multiple_rooms(self, mock_runtime: MagicMock) -> None:
        """Multiple rooms can be mapped."""
        room_map = RoomProjectMap(
            {
                "!room1:example.org": "myproject",
                "!room2:example.org": "other",
            },
            mock_runtime,
        )
        assert room_map.project_for_room("!room1:example.org") == "myproject"
        assert room_map.project_for_room("!room2:example.org") == "other"

    def test_invalid_project_ignored(self, mock_runtime: MagicMock) -> None:
        """Invalid project names are logged and ignored."""
        room_map = RoomProjectMap(
            {
                "!room1:example.org": "myproject",  # valid
                "!room2:example.org": "nonexistent",  # invalid
            },
            mock_runtime,
        )
        assert room_map.project_for_room("!room1:example.org") == "myproject"
        assert room_map.project_for_room("!room2:example.org") is None

    def test_context_for_room(self, mock_runtime: MagicMock) -> None:
        """context_for_room returns RunContext with project."""
        room_map = RoomProjectMap(
            {"!room:example.org": "myproject"},
            mock_runtime,
        )
        context = room_map.context_for_room("!room:example.org")
        assert context is not None
        assert context.project == "myproject"
        assert context.branch is None

    def test_context_for_room_unmapped(self, mock_runtime: MagicMock) -> None:
        """context_for_room returns None for unmapped room."""
        room_map = RoomProjectMap({}, mock_runtime)
        assert room_map.context_for_room("!room:example.org") is None

    def test_project_room_ids(self, mock_runtime: MagicMock) -> None:
        """project_room_ids returns all mapped room IDs."""
        room_map = RoomProjectMap(
            {
                "!room1:example.org": "myproject",
                "!room2:example.org": "other",
            },
            mock_runtime,
        )
        room_ids = room_map.project_room_ids()
        assert set(room_ids) == {"!room1:example.org", "!room2:example.org"}

    def test_all_mappings(self, mock_runtime: MagicMock) -> None:
        """all_mappings returns copy of mapping."""
        room_map = RoomProjectMap(
            {"!room:example.org": "myproject"},
            mock_runtime,
        )
        mappings = room_map.all_mappings()
        assert mappings == {"!room:example.org": "myproject"}
        # Should be a copy
        mappings["!other:example.org"] = "other"
        assert room_map.project_for_room("!other:example.org") is None


class TestBuildRoomProjectMap:
    def test_empty_config(self, mock_runtime: MagicMock) -> None:
        """Empty config produces empty mapping."""
        room_map = build_room_project_map({}, mock_runtime)
        assert room_map.project_room_ids() == ()

    def test_valid_config(self, mock_runtime: MagicMock) -> None:
        """Valid room_projects config is parsed."""
        config = {
            "room_projects": {
                "!room:example.org": "myproject",
            }
        }
        room_map = build_room_project_map(config, mock_runtime)
        assert room_map.project_for_room("!room:example.org") == "myproject"

    def test_invalid_room_projects_type(self, mock_runtime: MagicMock) -> None:
        """Non-dict room_projects is handled gracefully."""
        config = {"room_projects": "invalid"}
        room_map = build_room_project_map(config, mock_runtime)
        assert room_map.project_room_ids() == ()

    def test_invalid_entry_types(self, mock_runtime: MagicMock) -> None:
        """Non-string entries are filtered out."""
        config = {
            "room_projects": {
                "!room:example.org": "myproject",  # valid
                123: "other",  # invalid key
                "!room2:example.org": 456,  # invalid value
            }
        }
        room_map = build_room_project_map(config, mock_runtime)
        assert room_map.project_for_room("!room:example.org") == "myproject"
        assert len(room_map.project_room_ids()) == 1


class TestEngineDefaultsIntegration:
    """Test engine_defaults integration with room_project_map."""

    @pytest.mark.anyio
    async def test_engine_resolution_uses_room_project(
        self,
        mock_runtime: MagicMock,
    ) -> None:
        """Engine resolution considers room's bound project."""
        from takopi_matrix.engine_defaults import resolve_engine_for_message

        # Setup: room bound to project with specific default engine
        mock_runtime.default_engine = "haiku"
        mock_runtime.project_default_engine.side_effect = lambda ctx: (
            "opus" if ctx and ctx.project == "myproject" else None
        )

        room_map = RoomProjectMap(
            {"!room:example.org": "myproject"},
            mock_runtime,
        )

        result = await resolve_engine_for_message(
            runtime=mock_runtime,
            context=None,  # No explicit context
            explicit_engine=None,
            room_id="!room:example.org",
            room_prefs=None,
            room_project_map=room_map,
        )

        # Should use project's default engine
        assert result.engine == "opus"
        assert result.source == "project_default"

    @pytest.mark.anyio
    async def test_allowed_room_ids_includes_project_rooms(
        self,
        mock_runtime: MagicMock,
    ) -> None:
        """_allowed_room_ids includes project-bound rooms."""
        from takopi_matrix.engine_defaults import _allowed_room_ids

        room_map = RoomProjectMap(
            {"!project:example.org": "myproject"},
            mock_runtime,
        )

        allowed = _allowed_room_ids(
            ["!main:example.org"],  # explicitly configured
            mock_runtime,
            room_map,
        )

        assert "!main:example.org" in allowed
        assert "!project:example.org" in allowed


class TestResolveContextForRoom:
    def test_directive_takes_priority(self, mock_runtime: MagicMock) -> None:
        """Directive context overrides room binding."""
        from takopi_matrix.engine_defaults import resolve_context_for_room

        room_map = RoomProjectMap(
            {"!room:example.org": "myproject"},
            mock_runtime,
        )

        directive_ctx = RunContext(project="other", branch="feature")

        result = resolve_context_for_room(
            room_id="!room:example.org",
            directive_context=directive_ctx,
            room_project_map=room_map,
        )

        assert result == directive_ctx
        assert result.project == "other"
        assert result.branch == "feature"

    def test_room_binding_fallback(self, mock_runtime: MagicMock) -> None:
        """Room binding used when no directive."""
        from takopi_matrix.engine_defaults import resolve_context_for_room

        room_map = RoomProjectMap(
            {"!room:example.org": "myproject"},
            mock_runtime,
        )

        result = resolve_context_for_room(
            room_id="!room:example.org",
            directive_context=None,
            room_project_map=room_map,
        )

        assert result is not None
        assert result.project == "myproject"
        assert result.branch is None

    def test_no_context_available(self, mock_runtime: MagicMock) -> None:
        """Returns None when no context source available."""
        from takopi_matrix.engine_defaults import resolve_context_for_room

        room_map = RoomProjectMap({}, mock_runtime)

        result = resolve_context_for_room(
            room_id="!room:example.org",
            directive_context=None,
            room_project_map=room_map,
        )

        assert result is None
