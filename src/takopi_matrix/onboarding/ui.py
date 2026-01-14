"""UI components for onboarding wizard."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from typing import cast

import anyio
import questionary
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from questionary.constants import DEFAULT_QUESTION_PREFIX
from questionary.question import Question
from questionary.styles import merge_styles_default
from rich import box
from rich.console import Console
from rich.table import Table

from takopi.engines import list_backends
from takopi.logging import suppress_logs

from .discovery import _discover_homeserver, _test_login, _test_token


def _render_engine_table(console: Console) -> list[tuple[str, bool, str | None]]:
    """Render table of available engines and return list of (id, installed, install_cmd)."""
    backends = list_backends()
    rows: list[tuple[str, bool, str | None]] = []
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE)
    table.add_column("agent")
    table.add_column("status")
    table.add_column("install command")
    for backend in backends:
        cmd = backend.cli_cmd or backend.id
        installed = shutil.which(cmd) is not None
        status = (
            "[green]\u2713 installed[/]" if installed else "[dim]\u2717 not found[/]"
        )
        rows.append((backend.id, installed, backend.install_cmd))
        table.add_row(
            backend.id,
            status,
            "" if installed else (backend.install_cmd or "-"),
        )
    console.print(table)
    return rows


@contextmanager
def _suppress_logging():
    """Suppress logging during onboarding."""
    with suppress_logs():
        yield


def _confirm(message: str, *, default: bool = True) -> bool | None:
    """Custom yes/no confirmation prompt."""
    merged_style = merge_styles_default([None])
    status = {"answer": None, "complete": False}

    def get_prompt_tokens():
        tokens = [
            ("class:qmark", DEFAULT_QUESTION_PREFIX),
            ("class:question", f" {message} "),
        ]
        if not status["complete"]:
            tokens.append(("class:instruction", "(yes/no) "))
        if status["answer"] is not None:
            tokens.append(("class:answer", "yes" if status["answer"] else "no"))
        return to_formatted_text(tokens)

    def exit_with_result(event):
        status["complete"] = True
        event.app.exit(result=status["answer"])

    bindings = KeyBindings()

    @bindings.add(Keys.ControlQ, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add("n")
    @bindings.add("N")
    def key_n(event):
        status["answer"] = False
        exit_with_result(event)

    @bindings.add("y")
    @bindings.add("Y")
    def key_y(event):
        status["answer"] = True
        exit_with_result(event)

    @bindings.add(Keys.ControlH)
    def key_backspace(event):
        status["answer"] = None

    @bindings.add(Keys.ControlM, eager=True)
    def set_answer(event):
        if status["answer"] is None:
            status["answer"] = default
        exit_with_result(event)

    @bindings.add(Keys.Any)
    def other(event):
        _ = event

    question = Question(
        PromptSession(get_prompt_tokens, key_bindings=bindings, style=merged_style).app
    )
    return question.ask()


def _prompt_homeserver(console: Console) -> str | None:
    """Prompt for Matrix homeserver."""
    while True:
        server = questionary.text("enter your matrix server (e.g., matrix.org):").ask()
        if server is None:
            return None
        server = server.strip()
        if not server:
            console.print("  server cannot be empty")
            continue

        console.print("  discovering homeserver...")
        homeserver = anyio.run(_discover_homeserver, server)
        console.print(f"  found: {homeserver}")
        return homeserver


def _prompt_credentials(
    console: Console,
    homeserver: str,
) -> tuple[str, str, str | None] | None:
    """
    Prompt for Matrix credentials.

    Returns (user_id, access_token, device_id) or None.
    """
    user_id = questionary.text("enter your user ID (e.g., @bot:matrix.org):").ask()
    if user_id is None:
        return None
    user_id = user_id.strip()
    if not user_id:
        console.print("  user ID cannot be empty")
        return None

    if not user_id.startswith("@"):
        domain = homeserver.replace("https://", "").replace("http://", "").split("/")[0]
        user_id = f"@{user_id}:{domain}"
        console.print(f"  using: {user_id}")

    auth_method = questionary.select(
        "authentication method:",
        choices=["access token (recommended)", "password"],
    ).ask()
    if auth_method is None:
        return None

    if auth_method == "password":
        password = questionary.password("enter password:").ask()
        if password is None:
            return None

        console.print("  logging in...")
        # Cast to override anyio.run's incorrect type stub (returns None instead of func return type)
        result = cast(
            tuple[bool, str | None, str | None, str | None],
            anyio.run(_test_login, homeserver, user_id, password),
        )
        ok, token, device_id, error_msg = result
        if not ok or not token:
            if error_msg:
                console.print(f"  login failed: {error_msg}")
            else:
                console.print("  login failed")
            retry = _confirm("try again?", default=True)
            if retry:
                return _prompt_credentials(console, homeserver)
            return None

        console.print(f"  logged in (device: {device_id})")
        return user_id, token, device_id

    token = questionary.password("paste access token:").ask()
    if token is None:
        return None
    token = token.strip()

    console.print("  validating...")
    ok = anyio.run(_test_token, homeserver, user_id, token)
    if not ok:
        console.print("  token validation failed")
        retry = _confirm("try again?", default=True)
        if retry:
            return _prompt_credentials(console, homeserver)
        return None

    console.print("  token valid")
    return user_id, token, None
