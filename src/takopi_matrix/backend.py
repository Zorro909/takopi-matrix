from __future__ import annotations

import os
from pathlib import Path

import anyio

from takopi.api import (
    EngineBackend,
    ExecBridgeConfig,
    SetupResult,
    TransportBackend,
    TransportRuntime,
)
from .bridge import (
    MatrixBridgeConfig,
    MatrixFileDownloadConfig,
    MatrixPresenter,
    MatrixTransport,
    MatrixVoiceTranscriptionConfig,
    run_main_loop,
)
from .client import MatrixClient
from .onboarding import check_setup, interactive_setup
from .room_prefs import RoomPrefsStore, resolve_prefs_path
from .room_projects import build_room_project_map


def _get_crypto_store_path() -> Path:
    """Get the path for the E2EE crypto store."""
    return Path.home() / ".takopi" / "matrix_crypto.db"


def _build_startup_message(
    runtime: TransportRuntime,
    *,
    startup_pwd: str,
) -> str:
    available_engines = list(runtime.available_engine_ids())
    missing_engines = list(runtime.missing_engine_ids())
    engine_list = ", ".join(available_engines) if available_engines else "none"
    if missing_engines:
        engine_list = f"{engine_list} (not installed: {', '.join(missing_engines)})"
    project_aliases = sorted(set(runtime.project_aliases()), key=str.lower)
    project_list = ", ".join(project_aliases) if project_aliases else "none"
    return (
        f"\N{OCTOPUS} **takopi is ready**\n\n"
        f"default: `{runtime.default_engine}`  \n"
        f"agents: `{engine_list}`  \n"
        f"projects: `{project_list}`  \n"
        f"working in: `{startup_pwd}`"
    )


def _build_voice_transcription_config(
    transport_config: dict[str, object],
) -> MatrixVoiceTranscriptionConfig:
    return MatrixVoiceTranscriptionConfig(
        enabled=bool(transport_config.get("voice_transcription", False)),
    )


def _build_file_download_config(
    transport_config: dict[str, object],
) -> MatrixFileDownloadConfig:
    enabled = bool(transport_config.get("file_download", True))
    max_mb = transport_config.get("file_download_max_mb", 50)
    max_size = (
        int(max_mb) * 1024 * 1024
        if isinstance(max_mb, (int, float))
        else 50 * 1024 * 1024
    )
    return MatrixFileDownloadConfig(
        enabled=enabled,
        max_size_bytes=max_size,
    )


def _require_matrix_config(
    config: dict[str, object], config_path: Path
) -> tuple[str, str, str | None, str | None, list[str]]:
    """
    Extract and validate Matrix configuration.

    Returns (homeserver, user_id, access_token, password, room_ids).
    """
    from takopi.api import ConfigError

    homeserver = config.get("homeserver")
    if homeserver is None or not isinstance(homeserver, str) or not homeserver.strip():
        raise ConfigError(f"Missing `homeserver` in {config_path}.")

    user_id = config.get("user_id")
    if user_id is None or not isinstance(user_id, str) or not user_id.strip():
        raise ConfigError(f"Missing `user_id` in {config_path}.")

    access_token = config.get("access_token")
    if access_token is not None and not isinstance(access_token, str):
        raise ConfigError(
            f"Invalid `access_token` in {config_path}; expected a string."
        )

    password = config.get("password")
    if password is not None and not isinstance(password, str):
        raise ConfigError(f"Invalid `password` in {config_path}; expected a string.")

    if not access_token and not password:
        raise ConfigError(
            f"Missing authentication in {config_path}; "
            "provide either `access_token` or `password`."
        )

    room_ids = config.get("room_ids")
    if room_ids is None:
        raise ConfigError(f"Missing `room_ids` in {config_path}.")
    if not isinstance(room_ids, list) or not room_ids:
        raise ConfigError(
            f"Invalid `room_ids` in {config_path}; expected a non-empty list."
        )
    for room_id in room_ids:
        if not isinstance(room_id, str) or not room_id.startswith("!"):
            raise ConfigError(
                f"Invalid room_id {room_id!r} in {config_path}; "
                "must be a string starting with '!'."
            )

    return (
        homeserver.strip(),
        user_id.strip(),
        access_token.strip() if access_token else None,
        password.strip() if password else None,
        [str(r).strip() for r in room_ids],
    )


class MatrixBackend(TransportBackend):
    id = "matrix"
    description = "Matrix homeserver"

    def check_setup(
        self,
        engine_backend: EngineBackend,
        *,
        transport_override: str | None = None,
    ) -> SetupResult:
        return check_setup(engine_backend, transport_override=transport_override)

    def interactive_setup(self, *, force: bool) -> bool:
        return interactive_setup(force=force)

    def lock_token(
        self, *, transport_config: dict[str, object], _config_path: Path
    ) -> str | None:
        try:
            _, user_id, _, _, _ = _require_matrix_config(transport_config, _config_path)
            return user_id
        except Exception:
            return None

    def build_and_run(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        homeserver, user_id, access_token, password, room_ids = _require_matrix_config(
            transport_config, config_path
        )

        device_id = transport_config.get("device_id")
        if device_id is not None and not isinstance(device_id, str):
            device_id = None

        e2ee_enabled = transport_config.get("e2ee_enabled")
        crypto_store_path: Path | None = None
        if e2ee_enabled is True or e2ee_enabled is None:
            crypto_store_path = _get_crypto_store_path()
            raw_path = transport_config.get("crypto_store_path")
            if isinstance(raw_path, str) and raw_path.strip():
                crypto_store_path = Path(raw_path).expanduser()

        user_allowlist: set[str] | None = None
        raw_allowlist = transport_config.get("user_allowlist")
        if isinstance(raw_allowlist, list):
            user_allowlist = {
                str(u).strip() for u in raw_allowlist if isinstance(u, str)
            }

        startup_msg = _build_startup_message(
            runtime,
            startup_pwd=os.getcwd(),
        )

        client = MatrixClient(
            homeserver=homeserver,
            user_id=user_id,
            access_token=access_token,
            password=password,
            device_id=device_id,
            crypto_store_path=crypto_store_path,
        )

        transport = MatrixTransport(client)
        presenter = MatrixPresenter()

        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=final_notify,
        )

        voice_transcription = _build_voice_transcription_config(transport_config)
        file_download = _build_file_download_config(transport_config)
        send_startup_message = bool(transport_config.get("send_startup_message", True))

        # Initialize room preferences store for per-room engine defaults
        room_prefs_path = resolve_prefs_path(config_path)
        room_prefs = RoomPrefsStore(room_prefs_path)

        # Build room-to-project mapping from config
        room_project_map = build_room_project_map(transport_config, runtime)

        cfg = MatrixBridgeConfig(
            client=client,
            runtime=runtime,
            room_ids=room_ids,
            user_allowlist=user_allowlist,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
            voice_transcription=voice_transcription,
            file_download=file_download,
            send_startup_message=send_startup_message,
            room_prefs=room_prefs,
            room_project_map=room_project_map,
        )

        # anyio.run only accepts positional args, so pass as a lambda
        async def _run() -> None:
            await run_main_loop(cfg, default_engine_override=default_engine_override)

        anyio.run(_run)


matrix_backend = MatrixBackend()
BACKEND = matrix_backend
