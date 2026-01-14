"""Room-to-project mapping for Matrix.

Provides project binding for Matrix rooms, similar to telegram's chat_id → project mapping.
Configuration is done in [transports.matrix.room_projects] section:

```toml
[transports.matrix]
room_ids = ["!main:example.org", "!project:example.org"]

[transports.matrix.room_projects]
"!project:example.org" = "myproject"
```
"""

from __future__ import annotations

from takopi.api import RunContext, TransportRuntime
from takopi.logging import get_logger

logger = get_logger(__name__)


class RoomProjectMap:
    """Maps Matrix room IDs to project keys.

    This enables room-specific project binding, so messages in a room
    automatically use that project's context and working directory.
    """

    def __init__(
        self,
        room_projects: dict[str, str],
        runtime: TransportRuntime,
    ) -> None:
        """Initialize room-to-project mapping.

        Args:
            room_projects: Mapping of room_id → project_key from config.
            runtime: Transport runtime for project validation.
        """
        self._room_map: dict[str, str] = {}
        self._runtime = runtime

        # Validate and store mappings
        valid_projects = set(runtime.project_aliases())
        for room_id, project_key in room_projects.items():
            # Normalize project key (case-insensitive lookup)
            project_key.lower()
            actual_key = runtime.project_key_for_alias(project_key)

            if actual_key is not None:
                self._room_map[room_id] = actual_key
                logger.debug(
                    "matrix.room_projects.mapped",
                    room_id=room_id,
                    project=actual_key,
                )
            else:
                logger.warning(
                    "matrix.room_projects.invalid_project",
                    room_id=room_id,
                    project=project_key,
                    available=list(valid_projects),
                )

    def project_for_room(self, room_id: str) -> str | None:
        """Get the project key bound to a room.

        Args:
            room_id: Matrix room ID (e.g., "!abc123:example.org")

        Returns:
            Project key if room is bound, None otherwise.
        """
        return self._room_map.get(room_id)

    def context_for_room(self, room_id: str) -> RunContext | None:
        """Get the RunContext for a room's bound project.

        Args:
            room_id: Matrix room ID

        Returns:
            RunContext with project set if room is bound, None otherwise.
        """
        project_key = self.project_for_room(room_id)
        if project_key is None:
            return None
        return RunContext(project=project_key, branch=None)

    def project_room_ids(self) -> tuple[str, ...]:
        """Get all room IDs that are bound to projects.

        Returns:
            Tuple of room IDs with project bindings.
        """
        return tuple(self._room_map.keys())

    def all_mappings(self) -> dict[str, str]:
        """Get all room-to-project mappings.

        Returns:
            Copy of the room_id → project_key mapping.
        """
        return dict(self._room_map)


def build_room_project_map(
    transport_config: dict[str, object],
    runtime: TransportRuntime,
) -> RoomProjectMap:
    """Build RoomProjectMap from transport config.

    Args:
        transport_config: The [transports.matrix] config dict.
        runtime: Transport runtime for project validation.

    Returns:
        Configured RoomProjectMap instance.
    """
    raw_room_projects = transport_config.get("room_projects", {})

    # Validate it's a dict
    if not isinstance(raw_room_projects, dict):
        logger.warning(
            "matrix.room_projects.invalid_config",
            value_type=type(raw_room_projects).__name__,
            hint="Expected [transports.matrix.room_projects] to be a table",
        )
        raw_room_projects = {}

    # Convert to str → str, filtering invalid entries
    room_projects: dict[str, str] = {}
    for room_id, project in raw_room_projects.items():
        if isinstance(room_id, str) and isinstance(project, str):
            room_projects[room_id] = project
        else:
            logger.warning(
                "matrix.room_projects.invalid_entry",
                room_id=room_id,
                project=project,
                hint="Both room_id and project must be strings",
            )

    return RoomProjectMap(room_projects, runtime)
