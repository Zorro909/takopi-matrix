"""Command handling for Matrix transport.

This module provides command parsing, dispatch, and execution for the Matrix bridge.
"""

from __future__ import annotations

from .dispatch import dispatch_command
from .executor import MatrixCommandExecutor
from .parse import parse_slash_command, split_command_args

__all__ = [
    "dispatch_command",
    "MatrixCommandExecutor",
    "parse_slash_command",
    "split_command_args",
]
