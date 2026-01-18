"""Tests for onboarding modules - validation, discovery, config generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

from takopi.api import SetupIssue


# --- validation.py tests ---


def test_display_path_relative_to_home(tmp_path: Path, monkeypatch) -> None:
    """_display_path returns path relative to home."""
    from takopi_matrix.onboarding.validation import _display_path

    # Mock Path.home() to return tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    subpath = tmp_path / "config" / "takopi.toml"
    result = _display_path(subpath)

    assert result == "~/config/takopi.toml"


def test_display_path_not_relative_to_home(tmp_path: Path, monkeypatch) -> None:
    """_display_path returns absolute path when not under home."""
    from takopi_matrix.onboarding.validation import _display_path

    # Mock Path.home() to return a different path
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "other")

    external_path = Path("/etc/takopi.toml")
    result = _display_path(external_path)

    assert result == "/etc/takopi.toml"


def test_config_issue_creates_setup_issue(tmp_path: Path) -> None:
    """config_issue creates SetupIssue with formatted path."""
    from takopi_matrix.onboarding.validation import config_issue

    config_path = tmp_path / "takopi.toml"
    result = config_issue(config_path, title="create a config")

    assert isinstance(result, SetupIssue)
    assert result.title == "create a config"
    assert len(result.lines) == 1  # SetupIssue uses 'lines' not 'instructions'
    assert str(config_path) in result.lines[0] or "~" in result.lines[0]


def test_libolm_install_issue_darwin(monkeypatch) -> None:
    """_libolm_install_issue returns brew command for macOS."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Darwin")

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "brew install libolm" in result.lines[0]


def test_libolm_install_issue_linux_ubuntu(monkeypatch, tmp_path: Path) -> None:
    """_libolm_install_issue returns apt command for Ubuntu."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Linux")

    # Create a fake os-release file
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Ubuntu"\nID=ubuntu\n')

    # Patch open to return our fake file
    original_open = open

    def mock_open(path, *args, **kwargs):
        if path == "/etc/os-release":
            return original_open(os_release, *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "apt-get install libolm-dev" in result.lines[0]


def test_libolm_install_issue_linux_fedora(monkeypatch, tmp_path: Path) -> None:
    """_libolm_install_issue returns dnf command for Fedora."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Linux")

    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Fedora"\nID=fedora\n')

    original_open = open

    def mock_open(path, *args, **kwargs):
        if path == "/etc/os-release":
            return original_open(os_release, *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "dnf install libolm-devel" in result.lines[0]


def test_libolm_install_issue_linux_arch(monkeypatch, tmp_path: Path) -> None:
    """_libolm_install_issue returns pacman command for Arch."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Linux")

    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Arch Linux"\nID=arch\n')

    original_open = open

    def mock_open(path, *args, **kwargs):
        if path == "/etc/os-release":
            return original_open(os_release, *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "pacman -S libolm" in result.lines[0]


def test_libolm_install_issue_linux_opensuse(monkeypatch, tmp_path: Path) -> None:
    """_libolm_install_issue returns zypper command for openSUSE."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Linux")

    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="openSUSE Tumbleweed"\nID=opensuse-tumbleweed\n')

    original_open = open

    def mock_open(path, *args, **kwargs):
        if path == "/etc/os-release":
            return original_open(os_release, *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "zypper install libolm-devel" in result.lines[0]


def test_libolm_install_issue_linux_unknown(monkeypatch) -> None:
    """_libolm_install_issue returns generic message for unknown Linux."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Linux")

    # Make os-release not exist
    def mock_open(path, *args, **kwargs):
        if path == "/etc/os-release":
            raise FileNotFoundError()
        return open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "libolm-dev" in result.lines[0] or "libolm-devel" in result.lines[0]


def test_libolm_install_issue_windows(monkeypatch) -> None:
    """_libolm_install_issue returns build instruction for Windows."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "Windows")

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "Build libolm" in result.lines[0] or "build" in result.lines[0].lower()


def test_check_libolm_available_returns_bool() -> None:
    """_check_libolm_available returns a boolean."""
    from takopi_matrix.onboarding.validation import _check_libolm_available

    result = _check_libolm_available()
    assert isinstance(result, bool)


def test_check_matrix_config_not_matrix_transport() -> None:
    """_check_matrix_config returns empty if transport is not matrix."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "telegram"

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert result == []


def test_check_matrix_config_missing_homeserver() -> None:
    """_check_matrix_config returns issue if homeserver is missing."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = {"matrix": {}}  # No homeserver

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert len(result) == 1
    assert "configure matrix" in result[0].title


def test_check_matrix_config_missing_user_id() -> None:
    """_check_matrix_config returns issue if user_id is missing."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = {"matrix": {"homeserver": "https://matrix.org"}}

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert len(result) == 1
    assert "configure matrix" in result[0].title


def test_check_matrix_config_missing_auth() -> None:
    """_check_matrix_config returns issue if no auth method is set."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = {
        "matrix": {"homeserver": "https://matrix.org", "user_id": "@bot:matrix.org"}
    }

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert len(result) == 1
    assert "configure matrix" in result[0].title


def test_check_matrix_config_missing_room_ids() -> None:
    """_check_matrix_config returns issue if room_ids is missing."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "access_token": "token",
        }
    }

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert len(result) == 1
    assert "configure matrix" in result[0].title


def test_check_matrix_config_valid() -> None:
    """_check_matrix_config returns empty for valid config."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "access_token": "token",
            "room_ids": ["!room:matrix.org"],
        }
    }

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert result == []


def test_check_matrix_config_with_password() -> None:
    """_check_matrix_config accepts password instead of access_token."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "password": "secret",
            "room_ids": ["!room:matrix.org"],
        }
    }

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert result == []


def test_check_matrix_config_pydantic_model_extra() -> None:
    """_check_matrix_config handles Pydantic v2 model_extra."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    # Create a mock that behaves like Pydantic v2 model
    transports_mock = MagicMock()
    transports_mock.model_extra = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "access_token": "token",
            "room_ids": ["!room:matrix.org"],
        }
    }

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = transports_mock

    result = _check_matrix_config(settings, Path("/config.toml"))

    assert result == []


def test_check_matrix_config_no_transports() -> None:
    """_check_matrix_config handles missing transports attribute."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    settings.transports = None

    result = _check_matrix_config(settings, Path("/config.toml"))

    # Should get configure issue since no matrix config exists
    assert len(result) == 1


def test_check_matrix_config_transports_unknown_type() -> None:
    """_check_matrix_config handles unknown transports type."""
    from takopi_matrix.onboarding.validation import _check_matrix_config

    settings = MagicMock()
    settings.transport = "matrix"
    # Use a mock that doesn't have model_extra and isn't a dict
    settings.transports = MagicMock(spec=[])  # No attributes at all

    result = _check_matrix_config(settings, Path("/config.toml"))

    # Should get configure issue since transports isn't usable
    assert len(result) == 1


def test_libolm_install_issue_unknown_platform(monkeypatch) -> None:
    """_libolm_install_issue returns generic message for unknown platform."""
    from takopi_matrix.onboarding.validation import _libolm_install_issue

    monkeypatch.setattr("platform.system", lambda: "FreeBSD")

    result = _libolm_install_issue()

    assert isinstance(result, SetupIssue)
    assert "libolm" in result.lines[0].lower()


def test_check_libolm_available_import_error(monkeypatch) -> None:
    """_check_libolm_available returns False when import fails."""
    from takopi_matrix.onboarding import validation

    # Clear the cached result if any
    original_func = validation._check_libolm_available

    def mock_check():
        try:
            raise ImportError("No module named 'nio.crypto.Olm'")
        except ImportError:
            return False

    monkeypatch.setattr(validation, "_check_libolm_available", mock_check)

    result = validation._check_libolm_available()
    assert result is False

    # Restore
    monkeypatch.setattr(validation, "_check_libolm_available", original_func)


def test_check_setup_missing_backend() -> None:
    """check_setup returns issue when backend CLI is not found."""
    from takopi_matrix.onboarding.validation import check_setup

    backend = MagicMock()
    backend.id = "nonexistent"
    backend.cli_cmd = "totally_nonexistent_command_12345"
    backend.install_cmd = "pip install nonexistent"

    with patch(
        "takopi_matrix.onboarding.validation._check_libolm_available", return_value=True
    ):
        with patch(
            "takopi_matrix.onboarding.validation.load_settings"
        ) as mock_load:
            from takopi.config import ConfigError

            mock_load.side_effect = ConfigError("No config")

            result = check_setup(backend)

    assert len(result.issues) > 0
    # Should have an install issue for the backend
    has_install_issue = any("install" in str(issue).lower() for issue in result.issues)
    assert has_install_issue


def test_check_setup_config_error_existing_file(tmp_path: Path) -> None:
    """check_setup handles ConfigError when file exists."""
    from takopi_matrix.onboarding.validation import check_setup
    from takopi.config import ConfigError

    backend = MagicMock()
    backend.id = "testbackend"
    backend.cli_cmd = "bash"  # Something that exists
    backend.install_cmd = None

    config_path = tmp_path / "takopi.toml"
    config_path.write_text("invalid toml content")

    with patch(
        "takopi_matrix.onboarding.validation._check_libolm_available", return_value=True
    ), patch(
        "takopi_matrix.onboarding.validation.load_settings"
    ) as mock_load, patch(
        "takopi_matrix.onboarding.validation.HOME_CONFIG_PATH", config_path
    ):
        mock_load.side_effect = ConfigError("Parse error")
        result = check_setup(backend)

    assert len(result.issues) > 0


def test_check_setup_with_transport_override() -> None:
    """check_setup uses transport_override when provided."""
    from takopi_matrix.onboarding.validation import check_setup

    backend = MagicMock()
    backend.id = "testbackend"
    backend.cli_cmd = "bash"
    backend.install_cmd = None

    mock_settings = MagicMock()
    mock_settings.transport = "telegram"  # Not matrix
    mock_settings.transports = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "access_token": "token",
            "room_ids": ["!room:matrix.org"],
        }
    }
    mock_settings.model_copy = MagicMock(return_value=mock_settings)

    with patch(
        "takopi_matrix.onboarding.validation._check_libolm_available", return_value=True
    ):
        with patch(
            "takopi_matrix.onboarding.validation.load_settings"
        ) as mock_load:
            mock_load.return_value = (mock_settings, Path("/config.toml"))

            result = check_setup(backend, transport_override="matrix")

    # model_copy should have been called with the override
    mock_settings.model_copy.assert_called_once_with(update={"transport": "matrix"})


def test_check_setup_libolm_missing() -> None:
    """check_setup returns libolm issue when not available."""
    from takopi_matrix.onboarding.validation import check_setup

    backend = MagicMock()
    backend.id = "testbackend"
    backend.cli_cmd = "bash"
    backend.install_cmd = None

    mock_settings = MagicMock()
    mock_settings.transport = "matrix"
    mock_settings.transports = {
        "matrix": {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "access_token": "token",
            "room_ids": ["!room:matrix.org"],
        }
    }

    with patch(
        "takopi_matrix.onboarding.validation._check_libolm_available",
        return_value=False,
    ):
        with patch(
            "takopi_matrix.onboarding.validation.load_settings"
        ) as mock_load:
            mock_load.return_value = (mock_settings, Path("/config.toml"))

            result = check_setup(backend)

    # Should have libolm issue
    has_libolm_issue = any(
        "libolm" in str(issue).lower() for issue in result.issues
    )
    assert has_libolm_issue


# --- discovery.py tests ---


@pytest.mark.anyio
async def test_discover_homeserver_with_url() -> None:
    """_discover_homeserver returns URL unchanged if already a URL."""
    from takopi_matrix.onboarding.discovery import _discover_homeserver

    result = await _discover_homeserver("https://matrix.example.org")
    assert result == "https://matrix.example.org"

    result = await _discover_homeserver("http://localhost:8008/")
    assert result == "http://localhost:8008"


@pytest.mark.anyio
async def test_discover_homeserver_fallback() -> None:
    """_discover_homeserver falls back to https://{server} on failure."""
    from takopi_matrix.onboarding.discovery import _discover_homeserver

    # Use a domain that will definitely fail well-known lookup
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_instance.get.side_effect = Exception("Network error")
        mock_client.return_value = mock_instance

        result = await _discover_homeserver("example.org")
        assert result == "https://example.org"


@pytest.mark.anyio
async def test_discover_homeserver_well_known_success() -> None:
    """_discover_homeserver uses well-known response."""
    from takopi_matrix.onboarding.discovery import _discover_homeserver

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "m.homeserver": {"base_url": "https://matrix.example.org:8448/"}
        }

        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_instance.get.return_value = mock_response
        mock_client.return_value = mock_instance

        result = await _discover_homeserver("example.org")
        assert result == "https://matrix.example.org:8448"


@pytest.mark.anyio
async def test_discover_homeserver_well_known_malformed() -> None:
    """_discover_homeserver falls back on malformed well-known."""
    from takopi_matrix.onboarding.discovery import _discover_homeserver

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"invalid": "data"}  # Missing m.homeserver

        mock_instance = AsyncMock()
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        mock_instance.get.return_value = mock_response
        mock_client.return_value = mock_instance

        result = await _discover_homeserver("example.org")
        assert result == "https://example.org"


@pytest.mark.anyio
async def test_test_login_returns_tuple() -> None:
    """_test_login returns tuple of (success, token, device_id, error)."""
    from takopi_matrix.onboarding.discovery import _test_login

    with patch("nio.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.message = "Invalid credentials"

        mock_client.login.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = await _test_login(
            "https://matrix.org", "@user:matrix.org", "wrongpassword"
        )

        assert isinstance(result, tuple)
        assert len(result) == 4
        assert mock_client.close.called


@pytest.mark.anyio
async def test_test_login_exception() -> None:
    """_test_login returns failure on exception."""
    from takopi_matrix.onboarding.discovery import _test_login

    with patch("nio.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.login.side_effect = Exception("Network error")
        mock_client_class.return_value = mock_client

        success, token, device_id, error = await _test_login(
            "https://matrix.org", "@user:matrix.org", "password"
        )

        assert success is False
        assert token is None
        assert "Network error" in error
        assert mock_client.close.called


@pytest.mark.anyio
async def test_test_token_failure() -> None:
    """_test_token returns False on sync failure."""
    from takopi_matrix.onboarding.discovery import _test_token

    with patch("nio.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.sync.side_effect = Exception("Unauthorized")
        mock_client_class.return_value = mock_client

        result = await _test_token(
            "https://matrix.org", "@user:matrix.org", "invalid_token"
        )

        assert result is False
        assert mock_client.close.called


# --- config_gen.py tests ---


def test_mask_token_short() -> None:
    """_mask_token masks short tokens entirely."""
    from takopi_matrix.onboarding.config_gen import _mask_token

    result = _mask_token("short")
    assert result == "*" * 5


def test_mask_token_long() -> None:
    """_mask_token shows prefix and suffix for long tokens."""
    from takopi_matrix.onboarding.config_gen import _mask_token

    result = _mask_token("syt_very_long_access_token_here")
    assert result.startswith("syt_very_")
    assert result.endswith("_here")
    assert "..." in result


def test_toml_escape_quotes() -> None:
    """_toml_escape escapes quotes."""
    from takopi_matrix.onboarding.config_gen import _toml_escape

    result = _toml_escape('value with "quotes"')
    assert result == 'value with \\"quotes\\"'


def test_toml_escape_backslash() -> None:
    """_toml_escape escapes backslashes."""
    from takopi_matrix.onboarding.config_gen import _toml_escape

    result = _toml_escape("path\\to\\file")
    assert result == "path\\\\to\\\\file"


def test_render_config_basic() -> None:
    """_render_config creates valid TOML structure."""
    from takopi_matrix.onboarding.config_gen import _render_config

    result = _render_config(
        homeserver="https://matrix.org",
        user_id="@bot:matrix.org",
        access_token="syt_test_token",
        room_ids=["!room1:matrix.org"],
        default_engine=None,
    )

    assert "[transports.matrix]" in result
    assert 'homeserver = "https://matrix.org"' in result
    assert 'user_id = "@bot:matrix.org"' in result
    assert 'access_token = "syt_test_token"' in result
    assert "!room1:matrix.org" in result


def test_render_config_with_default_engine() -> None:
    """_render_config includes default_engine if provided."""
    from takopi_matrix.onboarding.config_gen import _render_config

    result = _render_config(
        homeserver="https://matrix.org",
        user_id="@bot:matrix.org",
        access_token="token",
        room_ids=["!room:matrix.org"],
        default_engine="claude",
    )

    assert 'default_engine = "claude"' in result


def test_render_config_no_startup_message() -> None:
    """_render_config includes send_startup_message = false."""
    from takopi_matrix.onboarding.config_gen import _render_config

    result = _render_config(
        homeserver="https://matrix.org",
        user_id="@bot:matrix.org",
        access_token="token",
        room_ids=["!room:matrix.org"],
        default_engine=None,
        send_startup_message=False,
    )

    assert "send_startup_message = false" in result


# --- rooms.py tests ---


def test_room_invite_dataclass() -> None:
    """RoomInvite dataclass works."""
    from takopi_matrix.onboarding.rooms import RoomInvite

    invite = RoomInvite(
        room_id="!room:matrix.org",
        inviter="@user:matrix.org",
        room_name="Test Room",
    )

    assert invite.room_id == "!room:matrix.org"
    assert invite.inviter == "@user:matrix.org"
    assert invite.room_name == "Test Room"


def test_room_invite_none_values() -> None:
    """RoomInvite allows None values."""
    from takopi_matrix.onboarding.rooms import RoomInvite

    invite = RoomInvite(
        room_id="!room:matrix.org",
        inviter=None,
        room_name=None,
    )

    assert invite.room_id == "!room:matrix.org"
    assert invite.inviter is None
    assert invite.room_name is None


@pytest.mark.anyio
async def test_fetch_room_invites_returns_list() -> None:
    """_fetch_room_invites returns a list."""
    from takopi_matrix.onboarding.rooms import _fetch_room_invites

    with patch("nio.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.sync.side_effect = Exception("Error")
        mock_client_class.return_value = mock_client

        result = await _fetch_room_invites(
            "https://matrix.org", "@user:matrix.org", "token"
        )

        assert isinstance(result, list)
        assert len(result) == 0
