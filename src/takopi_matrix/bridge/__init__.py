"""Matrix bridge package - transport, commands, and event processing."""

from .config import (
    MatrixBridgeConfig,
    MatrixVoiceTranscriptionConfig,
    MatrixFileDownloadConfig,
)
from .presenter import MatrixPresenter
from .transport import MatrixTransport
from .cancel import _CANCEL_REACTIONS, _is_cancel_command  # Used by tests
from .commands import _parse_slash_command  # Used by tests
from .runtime import run_main_loop

__all__ = [
    # Config
    "MatrixBridgeConfig",
    "MatrixVoiceTranscriptionConfig",
    "MatrixFileDownloadConfig",
    # Presenter
    "MatrixPresenter",
    # Transport
    "MatrixTransport",
    # Cancel (PRIVATE for tests)
    "_CANCEL_REACTIONS",  # Used by tests
    "_is_cancel_command",  # Used by tests
    # Commands (PRIVATE for tests)
    "_parse_slash_command",  # Used by tests
    # Runtime
    "run_main_loop",
]
