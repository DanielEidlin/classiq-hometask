"""An in-memory stand-in for the API's task store."""

import uuid

from api.domain.abstractions import DatabaseUnavailable
from api.domain.outcomes import TaskOutcome


class FakeTaskStore:
    """Keeps the writes made through it and answers reads with the outcomes the test arranges.

    It doesn't imitate the database: the statements are tested on the real store.
    """

    def __init__(self, outcomes: dict[uuid.UUID, TaskOutcome] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.reachable = True
        self.inserted: list[uuid.UUID] = []
        self.marked_published: list[uuid.UUID] = []
        self.deleted: list[uuid.UUID] = []

    async def insert_task(self, task_id: uuid.UUID, circuit: str) -> None:
        self.inserted.append(task_id)

    async def mark_published(self, task_id: uuid.UUID) -> None:
        self.marked_published.append(task_id)

    async def delete_task(self, task_id: uuid.UUID) -> None:
        self.deleted.append(task_id)

    async def fetch_outcome(self, task_id: uuid.UUID) -> TaskOutcome | None:
        return self.outcomes.get(task_id)

    async def is_reachable(self) -> bool:
        return self.reachable


class UnavailableTaskStore(FakeTaskStore):
    """A store whose database can't be reached: every call fails as the real store does then."""

    async def insert_task(self, task_id: uuid.UUID, circuit: str) -> None:
        raise DatabaseUnavailable

    async def fetch_outcome(self, task_id: uuid.UUID) -> TaskOutcome | None:
        raise DatabaseUnavailable

    async def is_reachable(self) -> bool:
        return False


class BrokenTaskStore(FakeTaskStore):
    """A store whose reads fail with an error the API doesn't expect, as a driver bug would."""

    async def fetch_outcome(self, task_id: uuid.UUID) -> TaskOutcome | None:
        raise RuntimeError("the database driver blew up")
