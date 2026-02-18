"""Command handling for Matrix transport.

This module provides command parsing, dispatch, and execution for the Matrix bridge.
"""

from __future__ import annotations

from .builtin import BUILTIN_COMMAND_IDS, handle_builtin_command
from .dispatch import dispatch_command
from .executor import MatrixCommandExecutor
from .parse import normalize_slash_prefix, parse_slash_command, split_command_args

__all__ = [
    "BUILTIN_COMMAND_IDS",
    "dispatch_command",
    "handle_builtin_command",
    "MatrixCommandExecutor",
    "normalize_slash_prefix",
    "parse_slash_command",
    "split_command_args",
]
