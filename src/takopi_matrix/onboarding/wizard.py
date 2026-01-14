"""Interactive setup wizard."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any, cast

import anyio
import questionary
from rich.console import Console
from rich.panel import Panel

from takopi.api import ConfigError
from takopi.config import HOME_CONFIG_PATH, ensure_table, read_config, write_config

from .config_gen import _mask_token, _render_config
from .rooms import (
    RoomInvite,
    _accept_room_invite,
    _fetch_room_invites,
    _send_confirmation,
)
from .ui import (
    _confirm,
    _prompt_credentials,
    _prompt_homeserver,
    _render_engine_table,
    _suppress_logging,
)
from .validation import _check_libolm_available, _display_path, _libolm_install_issue


@dataclass(frozen=True, slots=True)
class MatrixUserInfo:
    """User information from Matrix login."""

    user_id: str
    display_name: str | None
    device_id: str | None


def interactive_setup(*, force: bool) -> bool:
    """Run the interactive onboarding wizard."""
    console = Console()
    config_path = HOME_CONFIG_PATH

    if config_path.exists() and not force:
        console.print(
            f"config already exists at {_display_path(config_path)}. "
            "use --onboard to reconfigure."
        )
        return True

    if config_path.exists() and force:
        overwrite = _confirm(
            f"update existing config at {_display_path(config_path)}?",
            default=False,
        )
        if not overwrite:
            return False

    with _suppress_logging():
        panel = Panel(
            "let's set up your matrix bot.",
            title="welcome to takopi!",
            border_style="yellow",
            padding=(1, 2),
            expand=False,
        )
        console.print(panel)

        console.print("step 1: matrix homeserver\n")
        homeserver = _prompt_homeserver(console)
        if homeserver is None:
            return False

        console.print("\nstep 2: authentication\n")
        creds = _prompt_credentials(console, homeserver)
        if creds is None:
            return False
        user_id, access_token, device_id = creds

        console.print("\nstep 3: room selection\n")
        console.print("  invite your bot to a room, then accept the invite here")

        room_ids: list[str] = []

        while True:
            console.print("  fetching room invites...")
            invites = cast(
                list[RoomInvite],
                anyio.run(_fetch_room_invites, homeserver, user_id, access_token),
            )

            if not invites:
                console.print("  no pending invites found")
                action = questionary.select(
                    "what would you like to do?",
                    choices=[
                        "refresh invites",
                        "enter room ID manually",
                        "done selecting rooms" if room_ids else "skip (no rooms)",
                    ],
                ).ask()

                if action is None:
                    return False

                if action == "refresh invites":
                    continue

                if action == "enter room ID manually":
                    room_id = questionary.text(
                        "enter room ID (e.g., !abc123:matrix.org):"
                    ).ask()
                    if room_id and room_id.strip():
                        room_ids.append(room_id.strip())
                        console.print(f"  added: {room_id.strip()}")
                    continue

                # done or skip
                break

            # Build choices from invites
            choices: list[str] = []
            for invite in invites:
                label = invite.room_id
                if invite.room_name:
                    label = f"{invite.room_name} ({invite.room_id})"
                if invite.inviter:
                    label += f" from {invite.inviter}"
                choices.append(label)
            choices.append("refresh invites")
            choices.append("enter room ID manually")
            if room_ids:
                choices.append("done selecting rooms")

            selected = questionary.select(
                "select a room invite to accept:",
                choices=choices,
            ).ask()

            if selected is None:
                return False

            if selected == "refresh invites":
                continue

            if selected == "enter room ID manually":
                room_id = questionary.text(
                    "enter room ID (e.g., !abc123:matrix.org):"
                ).ask()
                if room_id and room_id.strip():
                    room_ids.append(room_id.strip())
                    console.print(f"  added: {room_id.strip()}")
                continue

            if selected == "done selecting rooms":
                break

            # Find the selected invite
            selected_invite: RoomInvite | None = None
            for invite in invites:
                label = invite.room_id
                if invite.room_name:
                    label = f"{invite.room_name} ({invite.room_id})"
                if invite.inviter:
                    label += f" from {invite.inviter}"
                if label == selected:
                    selected_invite = invite
                    break

            if selected_invite is None:
                continue

            console.print(f"  accepting invite to {selected_invite.room_id}...")
            accepted = anyio.run(
                _accept_room_invite,
                homeserver,
                user_id,
                access_token,
                selected_invite.room_id,
            )

            if accepted:
                room_ids.append(selected_invite.room_id)
                console.print(f"  [green]joined {selected_invite.room_id}[/]")
            else:
                console.print(f"  [red]failed to join {selected_invite.room_id}[/]")

            add_more = _confirm("add more rooms?", default=False)
            if not add_more:
                break

        if not room_ids:
            console.print("  [yellow]warning: no rooms selected[/]")
            proceed = _confirm("continue without rooms?", default=False)
            if not proceed:
                return False

        if room_ids:
            sent = anyio.run(
                _send_confirmation, homeserver, user_id, access_token, room_ids[0]
            )
            if sent:
                console.print("  sent confirmation message")
            else:
                console.print("  could not send confirmation message")

        console.print("\nstep 4: agent cli tools")
        rows = _render_engine_table(console)
        installed_ids = [engine_id for engine_id, installed, _ in rows if installed]

        default_engine: str | None = None
        if installed_ids:
            default_engine = questionary.select(
                "choose default agent:",
                choices=installed_ids,
            ).ask()
            if default_engine is None:
                return False
        else:
            console.print("no agents found on PATH. install one to continue.")
            save_anyway = _confirm("save config anyway?", default=False)
            if not save_anyway:
                return False

        console.print("\nstep 4.5: encryption support")

        # Check libolm availability (required for E2EE)
        if not _check_libolm_available():
            console.print("  [red]libolm not detected[/]")
            console.print("  E2EE requires the libolm system library")
            issue = _libolm_install_issue()
            for hint in issue.hints:
                console.print(f"  {hint}")
            console.print("\n  after installation, restart setup with --onboard")
            return False
        else:
            console.print("  [green]E2EE support detected[/] \u2713")

        console.print("\nstep 4.6: startup message")
        console.print("  takopi can send a status message when it starts")
        send_startup_message = _confirm(
            "send startup message when bot starts?",
            default=True,
        )
        if send_startup_message is None:
            return False

        config_preview = _render_config(
            homeserver,
            user_id,
            _mask_token(access_token),
            room_ids,
            default_engine,
            send_startup_message=send_startup_message,
        ).rstrip()
        console.print("\nstep 5: save configuration\n")
        console.print(f"  {_display_path(config_path)}\n")
        for line in config_preview.splitlines():
            console.print(f"  {line}")
        console.print("")

        save = _confirm(
            f"save this config to {_display_path(config_path)}?",
            default=True,
        )
        if not save:
            return False

        raw_config: dict[str, Any] = {}
        if config_path.exists():
            try:
                raw_config = read_config(config_path)
            except ConfigError as exc:
                console.print(f"[yellow]warning:[/] config is malformed: {exc}")
                backup = config_path.with_suffix(".toml.bak")
                try:
                    shutil.copyfile(config_path, backup)
                except OSError as copy_exc:
                    console.print(
                        f"[yellow]warning:[/] failed to back up config: {copy_exc}"
                    )
                else:
                    console.print(f"  backed up to {_display_path(backup)}")
                raw_config = {}

        merged = dict(raw_config)
        if default_engine is not None:
            merged["default_engine"] = default_engine
        merged["transport"] = "matrix"

        transports = ensure_table(merged, "transports", config_path=config_path)
        matrix = ensure_table(
            transports,
            "matrix",
            config_path=config_path,
            label="transports.matrix",
        )
        matrix["homeserver"] = homeserver
        matrix["user_id"] = user_id
        matrix["access_token"] = access_token
        matrix["room_ids"] = room_ids
        matrix["send_startup_message"] = send_startup_message
        if device_id:
            matrix["device_id"] = device_id

        merged.pop("bot_token", None)
        merged.pop("chat_id", None)

        write_config(merged, config_path)
        console.print(f"  config saved to {_display_path(config_path)}")

        done_panel = Panel(
            "setup complete. starting takopi...",
            border_style="green",
            padding=(1, 2),
            expand=False,
        )
        console.print("\n")
        console.print(done_panel)
        return True
