"""Matrix onboarding package - setup wizard and validation."""

from .validation import check_setup, config_issue
from .wizard import interactive_setup, MatrixUserInfo

__all__ = [
    "check_setup",
    "config_issue",
    "interactive_setup",
    "MatrixUserInfo",
]
