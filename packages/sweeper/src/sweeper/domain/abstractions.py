"""The interfaces the sweeper depends on, so it doesn't depend on their implementations."""

import uuid
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol

from pydantic import BaseModel


class StaleTask(BaseModel):
    id: uuid.UUID
    pending_for: timedelta


class UnpublishedBatch(Protocol):
    """Unpublished tasks, locked so no other sweeper takes them until the batch ends."""

    @property
    def task_ids(self) -> list[uuid.UUID]: ...

    async def mark_published(self, task_id: uuid.UUID) -> None: ...


class TaskStore(Protocol):
    """The sweeper's reads and writes of tasks."""

    def lock_unpublished(self, grace: timedelta) -> AbstractAsyncContextManager[UnpublishedBatch]:
        """Lock the tasks unpublished for longer than `grace`, skipping any another sweeper
        holds. The locks, and the marks made through the batch, last until the block exits."""
        ...

    async def find_stale(self, stale_after: timedelta) -> list[StaleTask]:
        """Tasks still pending longer than `stale_after` after they were created."""
        ...
