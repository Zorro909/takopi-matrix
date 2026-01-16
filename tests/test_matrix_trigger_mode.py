"""Tests for trigger mode functionality."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from takopi_matrix.trigger_mode import (
    resolve_trigger_mode,
    should_trigger_run,
)


class TestResolveTriggerMode:
    @pytest.mark.anyio
    async def test_default_mode_is_all(self) -> None:
        """Without room prefs, mode defaults to 'all'."""
        result = await resolve_trigger_mode(
            room_id="!room:example.org",
            room_prefs=None,
        )
        assert result == "all"

    @pytest.mark.anyio
    async def test_room_mode_mentions(self) -> None:
        """Room with mentions mode returns 'mentions'."""
        room_prefs = AsyncMock()
        room_prefs.get_trigger_mode = AsyncMock(return_value="mentions")

        result = await resolve_trigger_mode(
            room_id="!room:example.org",
            room_prefs=room_prefs,
        )
        assert result == "mentions"

    @pytest.mark.anyio
    async def test_room_mode_none_returns_all(self) -> None:
        """Room with no trigger mode returns 'all'."""
        room_prefs = AsyncMock()
        room_prefs.get_trigger_mode = AsyncMock(return_value=None)

        result = await resolve_trigger_mode(
            room_id="!room:example.org",
            room_prefs=room_prefs,
        )
        assert result == "all"


class TestShouldTriggerRun:
    """Tests for the should_trigger_run function."""

    @pytest.fixture
    def runtime(self) -> MagicMock:
        """Create a mock TransportRuntime."""
        runtime = MagicMock()
        runtime.available_engine_ids.return_value = ["claude", "codex"]
        runtime.project_aliases.return_value = ["myproject"]
        return runtime

    def test_user_id_mention_triggers(self, runtime: MagicMock) -> None:
        """Message containing user ID should trigger."""
        result = should_trigger_run(
            "Hey @takopi:matrix.org can you help?",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_display_name_mention_triggers(self, runtime: MagicMock) -> None:
        """Message containing display name should trigger."""
        result = should_trigger_run(
            "Hey Takopi Bot can you help?",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_display_name_case_insensitive(self, runtime: MagicMock) -> None:
        """Display name matching should be case insensitive."""
        result = should_trigger_run(
            "hey takopi bot please help",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_reply_to_bot_triggers(self, runtime: MagicMock) -> None:
        """Reply to bot message should trigger."""
        result = should_trigger_run(
            "thanks for the help",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=True,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_slash_command_triggers(self, runtime: MagicMock) -> None:
        """Slash command in command_ids should trigger."""
        result = should_trigger_run(
            "/help how do I use this?",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids={"help"},
            reserved_room_commands=set(),
        )
        assert result is True

    def test_reserved_command_triggers(self, runtime: MagicMock) -> None:
        """Reserved command should trigger."""
        result = should_trigger_run(
            "/cancel",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands={"cancel"},
        )
        assert result is True

    def test_engine_id_triggers(self, runtime: MagicMock) -> None:
        """Message starting with engine ID as command should trigger."""
        result = should_trigger_run(
            "/claude explain this code",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_project_alias_triggers(self, runtime: MagicMock) -> None:
        """Message starting with project alias as command should trigger."""
        result = should_trigger_run(
            "/myproject run tests",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_random_message_does_not_trigger(self, runtime: MagicMock) -> None:
        """Random message without mention or command should not trigger."""
        result = should_trigger_run(
            "just chatting with friends",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is False

    def test_unknown_command_does_not_trigger(self, runtime: MagicMock) -> None:
        """Unknown slash command should not trigger."""
        result = should_trigger_run(
            "/unknowncommand",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is False

    def test_no_display_name_still_matches_user_id(self, runtime: MagicMock) -> None:
        """Even without display name, user ID mention should work."""
        result = should_trigger_run(
            "hello @takopi:matrix.org",
            own_user_id="@takopi:matrix.org",
            own_display_name=None,
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids=set(),
            reserved_room_commands=set(),
        )
        assert result is True

    def test_command_at_mention_handled(self, runtime: MagicMock) -> None:
        """Commands like /help@bot should work (@ suffix stripped by parser)."""
        result = should_trigger_run(
            "/help@takopi extra args",
            own_user_id="@takopi:matrix.org",
            own_display_name="Takopi Bot",
            reply_to_is_bot=False,
            runtime=runtime,
            command_ids={"help"},
            reserved_room_commands=set(),
        )
        assert result is True
