"""Bridge configuration classes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from takopi.api import ExecBridgeConfig, TransportRuntime

from ..files import MAX_FILE_SIZE

if TYPE_CHECKING:
    from ..client import MatrixClient
    from ..room_prefs import RoomPrefsStore
    from ..room_projects import RoomProjectMap


@dataclass(frozen=True)
class MatrixVoiceTranscriptionConfig:
    """Configuration for voice message transcription."""

    enabled: bool = False


@dataclass(frozen=True)
class MatrixFileDownloadConfig:
    """Configuration for file download handling."""

    enabled: bool = True
    max_size_bytes: int = MAX_FILE_SIZE
    download_dir: Path | None = None


@dataclass(frozen=True)
class MatrixBridgeConfig:
    """Main configuration for the Matrix bridge."""

    client: MatrixClient
    runtime: TransportRuntime
    room_ids: list[str]
    user_allowlist: set[str] | None
    startup_msg: str
    exec_cfg: ExecBridgeConfig
    voice_transcription: MatrixVoiceTranscriptionConfig | None = None
    file_download: MatrixFileDownloadConfig | None = None
    send_startup_message: bool = True
    room_prefs: RoomPrefsStore | None = None
    room_project_map: RoomProjectMap | None = None
