"""An in-memory stand-in for the sweeper's task store."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sweeper.abstractions import StaleTask


class FakeUnpublishedBatch:
    def __init__(self, task_ids: list[uuid.UUID], marked_published: list[uuid.UUID]) -> None:
        self._task_ids = task_ids
        self._marked_published = marked_published

    @property
    def task_ids(self) -> list[uuid.UUID]:
        return self._task_ids

    async def mark_published(self, task_id: uuid.UUID) -> None:
        self._marked_published.append(task_id)


class FakeTaskStore:
    """Answers with the tasks the test arranges and keeps the ones marked published.

    It doesn't imitate the database: locking is tested on the real store.
    """

    def __init__(
        self, unpublished: list[uuid.UUID] | None = None, stale: list[StaleTask] | None = None
    ) -> None:
        self._unpublished = unpublished or []
        self._stale = stale or []
        self.marked_published: list[uuid.UUID] = []
        self.batch_open = False

    @asynccontextmanager
    async def lock_unpublished(self, grace: timedelta) -> AsyncIterator[FakeUnpublishedBatch]:
        self.batch_open = True
        try:
            yield FakeUnpublishedBatch(self._unpublished, self.marked_published)
        finally:
            self.batch_open = False

    async def find_stale(self, stale_after: timedelta) -> list[StaleTask]:
        return self._stale
