"""Tests for Matrix backend configuration and setup."""

from __future__ import annotations

from pathlib import Path

import pytest

from takopi.config import ConfigError
from takopi_matrix.backend import (
    _build_file_download_config,
    _build_voice_transcription_config,
    _require_matrix_config,
)


class TestRequireMatrixConfig:
    """Test _require_matrix_config validation."""

    def test_valid_config_with_access_token(self) -> None:
        """Valid config with access_token succeeds."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": ["!abc:example.org"],
        }
        homeserver, user_id, token, password, rooms = _require_matrix_config(
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
        homeserver, user_id, token, password, rooms = _require_matrix_config(
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
            _require_matrix_config(config, Path("test.toml"))

    def test_missing_user_id_raises(self) -> None:
        """Missing user_id raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "access_token": "syt_...",
            "room_ids": ["!abc:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing `user_id`"):
            _require_matrix_config(config, Path("test.toml"))

    def test_missing_auth_raises(self) -> None:
        """Missing both access_token and password raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "room_ids": ["!abc:example.org"],
        }
        with pytest.raises(ConfigError, match="Missing authentication"):
            _require_matrix_config(config, Path("test.toml"))

    def test_missing_room_ids_raises(self) -> None:
        """Missing room_ids raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
        }
        with pytest.raises(ConfigError, match="Missing `room_ids`"):
            _require_matrix_config(config, Path("test.toml"))

    def test_empty_room_ids_raises(self) -> None:
        """Empty room_ids list raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": [],
        }
        with pytest.raises(ConfigError, match="expected a non-empty list"):
            _require_matrix_config(config, Path("test.toml"))

    def test_invalid_room_id_format_raises(self) -> None:
        """Room ID not starting with '!' raises ConfigError."""
        config = {
            "homeserver": "https://matrix.example.org",
            "user_id": "@bot:example.org",
            "access_token": "syt_...",
            "room_ids": ["invalid-room-id"],
        }
        with pytest.raises(ConfigError, match="must be a string starting with '!'"):
            _require_matrix_config(config, Path("test.toml"))

    def test_whitespace_trimming(self) -> None:
        """Whitespace is trimmed from config values."""
        config = {
            "homeserver": "  https://matrix.example.org  ",
            "user_id": "  @bot:example.org  ",
            "access_token": "  syt_...  ",
            "room_ids": [
                "!abc:example.org"
            ],  # Note: room_ids are not trimmed by _require_matrix_config
        }
        homeserver, user_id, token, password, rooms = _require_matrix_config(
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
        result = _build_voice_transcription_config(config)
        assert result.enabled is True

    def test_enabled_false(self) -> None:
        """voice_transcription=false creates disabled config."""
        config = {"voice_transcription": False}
        result = _build_voice_transcription_config(config)
        assert result.enabled is False

    def test_default_false(self) -> None:
        """Missing voice_transcription defaults to disabled."""
        config = {}
        result = _build_voice_transcription_config(config)
        assert result.enabled is False


class TestFileDownloadConfig:
    """Test file download config builder."""

    def test_enabled_true(self) -> None:
        """file_download=true creates enabled config."""
        config = {"file_download": True}
        result = _build_file_download_config(config)
        assert result.enabled is True

    def test_enabled_false(self) -> None:
        """file_download=false creates disabled config."""
        config = {"file_download": False}
        result = _build_file_download_config(config)
        assert result.enabled is False

    def test_default_true(self) -> None:
        """Missing file_download defaults to enabled."""
        config = {}
        result = _build_file_download_config(config)
        assert result.enabled is True

    def test_max_size_custom(self) -> None:
        """Custom file_download_max_mb sets max_size_bytes."""
        config = {"file_download_max_mb": 100}
        result = _build_file_download_config(config)
        assert result.max_size_bytes == 100 * 1024 * 1024

    def test_max_size_default(self) -> None:
        """Default file_download_max_mb is 50MB."""
        config = {}
        result = _build_file_download_config(config)
        assert result.max_size_bytes == 50 * 1024 * 1024
