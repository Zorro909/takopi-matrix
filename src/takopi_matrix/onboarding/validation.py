"""Setup validation and libolm checks."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from takopi.api import (
    ConfigError,
    EngineBackend,
    SetupIssue,
    SetupResult,
)
from takopi.backends_helpers import install_issue
from takopi.config import HOME_CONFIG_PATH
from takopi.settings import load_settings


def _check_libolm_available() -> bool:
    """Check if libolm is available (E2EE support works)."""
    try:
        from nio.crypto import Olm  # noqa: F401

        return True
    except ImportError:
        return False


def _libolm_install_issue() -> SetupIssue:
    """Create setup issue for missing libolm."""
    import platform

    system = platform.system().lower()

    if system == "darwin":
        install_cmd = "brew install libolm"
    elif system == "linux":
        # Try to detect distro
        try:
            with open("/etc/os-release") as f:
                os_release = f.read().lower()
        except FileNotFoundError:
            os_release = ""

        if "ubuntu" in os_release or "debian" in os_release:
            install_cmd = "sudo apt-get install libolm-dev"
        elif "fedora" in os_release or "rhel" in os_release or "centos" in os_release:
            install_cmd = "sudo dnf install libolm-devel"
        elif "arch" in os_release:
            install_cmd = "sudo pacman -S libolm"
        elif "opensuse" in os_release or "suse" in os_release:
            install_cmd = "sudo zypper install libolm-devel"
        else:
            install_cmd = "Install libolm-dev (or libolm-devel) for your distro"
    elif system == "windows":
        install_cmd = "Build libolm from source (see docs)"
    else:
        install_cmd = "Install libolm for your platform"

    return SetupIssue(
        "install libolm (E2EE dependency)",
        (f"   {install_cmd}",),
    )


def _display_path(path: Path) -> str:
    """Display path relative to home if possible."""
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


_CREATE_CONFIG_TITLE = "create a config"
_CONFIGURE_MATRIX_TITLE = "configure matrix"


def config_issue(path: Path, *, title: str) -> SetupIssue:
    """Create a setup issue for config file."""
    return SetupIssue(title, (f"   {_display_path(path)}",))


def _check_matrix_config(settings: Any, config_path: Path) -> list[SetupIssue]:
    """Validate Matrix configuration."""
    issues: list[SetupIssue] = []

    if settings.transport != "matrix":
        return issues

    transports = getattr(settings, "transports", None)
    if transports is None:
        transport_config = {}
    elif hasattr(transports, "model_extra"):
        # Pydantic v2 model with extra fields
        transport_config = transports.model_extra.get("matrix", {})
    elif isinstance(transports, dict):
        transport_config = transports.get("matrix", {})
    else:
        transport_config = {}

    if not transport_config.get("homeserver"):
        issues.append(config_issue(config_path, title=_CONFIGURE_MATRIX_TITLE))
        return issues

    if not transport_config.get("user_id"):
        issues.append(config_issue(config_path, title=_CONFIGURE_MATRIX_TITLE))
        return issues

    if not transport_config.get("access_token") and not transport_config.get(
        "password"
    ):
        issues.append(config_issue(config_path, title=_CONFIGURE_MATRIX_TITLE))
        return issues

    room_ids = transport_config.get("room_ids")
    if not room_ids or not isinstance(room_ids, list):
        issues.append(config_issue(config_path, title=_CONFIGURE_MATRIX_TITLE))
        return issues

    return issues


def check_setup(
    backend: EngineBackend,
    *,
    transport_override: str | None = None,
) -> SetupResult:
    """Check setup status for Matrix transport."""
    issues: list[SetupIssue] = []
    config_path = HOME_CONFIG_PATH
    cmd = backend.cli_cmd or backend.id
    backend_issues: list[SetupIssue] = []

    if shutil.which(cmd) is None:
        backend_issues.append(install_issue(cmd, backend.install_cmd))

    if not _check_libolm_available():
        issues.append(_libolm_install_issue())

    try:
        settings, config_path = load_settings()
        if transport_override:
            settings = settings.model_copy(update={"transport": transport_override})

        matrix_issues = _check_matrix_config(settings, config_path)
        if matrix_issues:
            issues.extend(matrix_issues)

    except ConfigError:
        issues.extend(backend_issues)
        title = (
            _CONFIGURE_MATRIX_TITLE
            if config_path.exists() and config_path.is_file()
            else _CREATE_CONFIG_TITLE
        )
        issues.append(config_issue(config_path, title=title))
        return SetupResult(issues=issues, config_path=config_path)

    issues.extend(backend_issues)
    return SetupResult(issues=issues, config_path=config_path)
