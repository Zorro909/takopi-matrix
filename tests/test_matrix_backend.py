"""Tests for Matrix backend configuration and setup."""

from __future__ import annotations

from pathlib import Path

import pytest

from takopi.config import ConfigError
from takopi_matrix.backend import (
    build_file_download_config,
    build_session_mode_config,
    build_voice_transcription_config,
    validate_matrix_config,
)


class TestRequireMatrixConfig:
    """Test validate_matrix_config validation."""

    def test_valid_config_with_access_token(self) -> None:
        """Valid config with access_token succeeds."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": ["!abc:example.org"],
        }
        homeserver, user_id, token, password, rooms = validate_matrix_config(
            config, Path("test.toml")
        )
        assert homeserver == "https://matrix.example.org"
        assert user_id == "@bot:example.org"
        assert token == "syt_..."
        assert password is None
        assert rooms == ["!abc:example.org"]

    def test_valid_config_with_password(self) -> None:
        """Valid config with password succeeds."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "password": "secret",
            "room_ids": ["!abc:example.org"],
        }
        homeserver, user_id, token, password, rooms = validate_matrix_config(
            config, Path("test.toml")
        )
        assert token is None
        assert password == "secret"

    def test_missing_homeserver_raises(self) -> None:
        """Missing homeserver raises ConfigError."""
        config = {
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": ["!abc:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `homeserver`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_missing_user_id_raises(self) -> None:
        """Missing user_id raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "access_token": "syt_...",
            "room_ids": ["!abc:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `user_id`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_missing_auth_raises(self) -> None:
        """Missing both access_token and password raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "room_ids": ["!abc:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing authentication"):
            validate_matrix_config(config, Path("test.toml"))

    def test_missing_room_ids_raises(self) -> None:
        """Missing room_ids raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
        }
        with pytest.raises(ConfigError, match="Missing `room_ids`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_empty_room_ids_raises(self) -> None:
        """Empty room_ids list raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": [],
        }
        with pytest.raises(ConfigError, match="expected a non-empty list"):
            validate_matrix_config(config, Path("test.toml"))

    def test_invalid_room_id_format_raises(self) -> None:
        """Room ID not starting with '!' raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": ["invalid-room-id"],
        }
        with pytest.raises(ConfigError, match="must be a string starting with '!'"):
            validate_matrix_config(config, Path("test.toml"))

    def test_whitespace_trimming(self) -> None:
        """Whitespace is trimmed from config values."""
        config = {
            "homeserver": "  https://matrix.example.org  ",
            "user_id": "  @bot:example.org  ",
            "access_token": "  syt_...  ",
            "room_ids": [
                "!abc:example.org"
            ],  # Note: room_ids are not trimmed by validate_matrix_config
        }
        homeserver, user_id, token, password, rooms = validate_matrix_config(
            config, Path("test.toml")
        )
        assert homeserver == "https://matrix.example.org"
        assert user_id == "@bot:example.org"
        assert token == "syt_..."
        assert rooms == ["!abc:example.org"]


class TestVoiceTranscriptionConfig:
    """Test voice transcription config builder."""

    def test_enabled_true(self) -> None:
        """voice_transcription=true creates enabled config."""
        config = {"voice_transcription": True}
        result = build_voice_transcription_config(config)
        assert result.enabled is True

    def test_enabled_false(self) -> None:
        """voice_transcription=false creates disabled config."""
        config = {"voice_transcription": False}
        result = build_voice_transcription_config(config)
        assert result.enabled is False

    def test_default_false(self) -> None:
        """Missing voice_transcription defaults to disabled."""
        config = {}
        result = build_voice_transcription_config(config)
        assert result.enabled is False


class TestFileDownloadConfig:
    """Test file download config builder."""

    def test_enabled_true(self) -> None:
        """file_download=true creates enabled config."""
        config = {"file_download": True}
        result = build_file_download_config(config)
        assert result.enabled is True

    def test_enabled_false(self) -> None:
        """file_download=false creates disabled config."""
        config = {"file_download": False}
        result = build_file_download_config(config)
        assert result.enabled is False

    def test_default_true(self) -> None:
        """Missing file_download defaults to enabled."""
        config = {}
        result = build_file_download_config(config)
        assert result.enabled is True

    def test_max_size_custom(self) -> None:
        """Custom file_download_max_mb sets max_size_bytes."""
        config = {"file_download_max_mb": 100}
        result = build_file_download_config(config)
        assert result.max_size_bytes == 100 * 1024 * 1024

    def test_max_size_default(self) -> None:
        """Default file_download_max_mb is 50MB."""
        config = {}
        result = build_file_download_config(config)
        assert result.max_size_bytes == 50 * 1024 * 1024

    def test_max_size_float(self) -> None:
        """Float max_size_mb is converted to int."""
        config = {"file_download_max_mb": 25.5}
        result = build_file_download_config(config)
        assert result.max_size_bytes == 25 * 1024 * 1024

    def test_max_size_invalid_type(self) -> None:
        """Invalid type for max_size_mb uses default."""
        config = {"file_download_max_mb": "invalid"}
        result = build_file_download_config(config)
        assert result.max_size_bytes == 50 * 1024 * 1024


class TestBuildStartupMessage:
    """Test _build_startup_message helper."""

    def test_startup_message_basic(self) -> None:
        """Startup message includes engine and project info."""
        from unittest.mock import MagicMock

        from takopi_matrix.backend import _build_startup_message

        runtime = MagicMock()
        runtime.available_engine_ids.return_value = ["claude", "codex"]
        runtime.missing_engine_ids.return_value = []
        runtime.project_aliases.return_value = ["proj1", "proj2"]
        runtime.default_engine = "claude"

        result = _build_startup_message(runtime, startup_pwd="/home/test")

        assert "takopi is ready" in result
        assert "claude" in result
        assert "codex" in result
        assert "proj1" in result
        assert "proj2" in result
        assert "/home/test" in result

    def test_startup_message_with_missing_engines(self) -> None:
        """Startup message shows missing engines."""
        from unittest.mock import MagicMock

        from takopi_matrix.backend import _build_startup_message

        runtime = MagicMock()
        runtime.available_engine_ids.return_value = ["claude"]
        runtime.missing_engine_ids.return_value = ["codex"]
        runtime.project_aliases.return_value = []
        runtime.default_engine = "claude"

        result = _build_startup_message(runtime, startup_pwd="/home")

        assert "claude" in result
        assert "not installed: codex" in result

    def test_startup_message_no_engines(self) -> None:
        """Startup message with no engines shows 'none'."""
        from unittest.mock import MagicMock

        from takopi_matrix.backend import _build_startup_message

        runtime = MagicMock()
        runtime.available_engine_ids.return_value = []
        runtime.missing_engine_ids.return_value = []
        runtime.project_aliases.return_value = []
        runtime.default_engine = None

        result = _build_startup_message(runtime, startup_pwd="/home")

        assert "agents: `none`" in result
        assert "projects: `none`" in result

    def test_startup_message_projects_sorted(self) -> None:
        """Project list is sorted case-insensitively."""
        from unittest.mock import MagicMock

        from takopi_matrix.backend import _build_startup_message

        runtime = MagicMock()
        runtime.available_engine_ids.return_value = ["claude"]
        runtime.missing_engine_ids.return_value = []
        runtime.project_aliases.return_value = ["Zebra", "apple", "Banana"]
        runtime.default_engine = "claude"

        result = _build_startup_message(runtime, startup_pwd="/home")

        assert "apple, Banana, Zebra" in result


class TestCryptoStorePath:
    """Test _get_crypto_store_path helper."""

    def test_crypto_store_path(self) -> None:
        """Crypto store path is in home directory."""
        from takopi_matrix.backend import _get_crypto_store_path

        result = _get_crypto_store_path()
        assert result.name == "matrix_crypto.db"
        assert ".takopi" in str(result)


class TestValidateMatrixConfigExtended:
    """Extended tests for validate_matrix_config."""

    def test_empty_homeserver_raises(self) -> None:
        """Empty homeserver string raises ConfigError."""
        config = {
            "homeserver": "   ",
            "user_id": "@bot:example.org",
            "access_token": "token",
            "room_ids": ["!room:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `homeserver`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_empty_user_id_raises(self) -> None:
        """Empty user_id string raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "   ",
            "access_token": "token",
            "room_ids": ["!room:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `user_id`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_invalid_access_token_type_raises(self) -> None:
        """Non-string access_token raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": 12345,  # Not a string
            "room_ids": ["!room:example.org"],
        }
        with pytest.raises(ConfigError, match="Invalid `access_token`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_invalid_password_type_raises(self) -> None:
        """Non-string password raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "password": 12345,  # Not a string
            "room_ids": ["!room:example.org"],
        }
        with pytest.raises(ConfigError, match="Invalid `password`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_room_ids_not_list_raises(self) -> None:
        """room_ids not a list raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "token",
            "room_ids": "!room:example.org",  # String, not list
        }
        with pytest.raises(ConfigError, match="expected a non-empty list"):
            validate_matrix_config(config, Path("test.toml"))

    def test_homeserver_not_string_raises(self) -> None:
        """Non-string homeserver raises ConfigError."""
        config = {
            "homeserver": 12345,
            "user_id": "@bot:example.org",
            "access_token": "token",
            "room_ids": ["!room:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `homeserver`"):
            validate_matrix_config(config, Path("test.toml"))

    def test_user_id_not_string_raises(self) -> None:
        """Non-string user_id raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": 12345,
            "access_token": "token",
            "room_ids": ["!room:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `user_id`"):
            validate_matrix_config(config, Path("test.toml"))


class TestMatrixBackend:
    """Test MatrixBackend class."""

    def test_backend_id(self) -> None:
        """Backend has correct id."""
        from takopi_matrix.backend import MatrixBackend

        backend = MatrixBackend()
        assert backend.id == "matrix"

    def test_backend_description(self) -> None:
        """Backend has correct description."""
        from takopi_matrix.backend import MatrixBackend

        backend = MatrixBackend()
        assert backend.description == "Matrix homeserver"

    def test_lock_token_valid_config(self) -> None:
        """lock_token returns user_id for valid config."""
        from takopi_matrix.backend import MatrixBackend

        backend = MatrixBackend()
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "token",
            "room_ids": ["!room:example.org"],
        }
        result = backend.lock_token(
            transport_config=config, _config_path=Path("test.toml")
        )
        assert result == "@bot:example.org"

    def test_lock_token_invalid_config(self) -> None:
        """lock_token returns None for invalid config."""
        from takopi_matrix.backend import MatrixBackend

        backend = MatrixBackend()
        config = {"invalid": "config"}
        result = backend.lock_token(
            transport_config=config, _config_path=Path("test.toml")
        )
        assert result is None

    def test_lock_token_not_dict(self) -> None:
        """lock_token returns None for non-dict config."""
        from takopi_matrix.backend import MatrixBackend

        backend = MatrixBackend()
        result = backend.lock_token(
            transport_config="not a dict", _config_path=Path("test.toml")
        )
        assert result is None


class TestSessionModeConfig:
    """Test session mode config builder."""

    def test_default_stateless(self) -> None:
        config = {}
        assert build_session_mode_config(config) == "stateless"

    def test_chat_explicit(self) -> None:
        config = {"session_mode": "chat"}
        assert build_session_mode_config(config) == "chat"

    def test_stateless_explicit(self) -> None:
        config = {"session_mode": "stateless"}
        assert build_session_mode_config(config) == "stateless"

    def test_invalid_falls_back_to_stateless(self) -> None:
        config = {"session_mode": "invalid"}
        assert build_session_mode_config(config) == "stateless"
