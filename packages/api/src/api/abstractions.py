"""The interfaces the API's routes depend on, so they don't depend on their implementations."""

import uuid
from typing import Protocol

from api.models import TaskOutcome


class TaskStore(Protocol):
    """The API's reads and writes of tasks. Each call is its own transaction."""

    async def insert_task(self, task_id: uuid.UUID, circuit: str) -> None: ...

    async def mark_published(self, task_id: uuid.UUID) -> None: ...

    async def delete_task(self, task_id: uuid.UUID) -> None: ...

    async def fetch_outcome(self, task_id: uuid.UUID) -> TaskOutcome | None:
        """The task's stored outcome, or None when it doesn't exist."""
        ...

    async def is_reachable(self) -> bool: ...
