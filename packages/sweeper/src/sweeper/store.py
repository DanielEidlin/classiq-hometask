"""The sweeper's task store on Postgres."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from common.tasks import TaskStatus, tasks
from sweeper.abstractions import StaleTask


class PostgresUnpublishedBatch:
    """Marks tasks published in the transaction holding their locks. A separate transaction
    would wait for those same locks, which are released only when the batch ends."""

    def __init__(self, connection: AsyncConnection, task_ids: list[uuid.UUID]) -> None:
        self._connection = connection
        self._task_ids = task_ids

    @property
    def task_ids(self) -> list[uuid.UUID]:
        return self._task_ids

    async def mark_published(self, task_id: uuid.UUID) -> None:
        await self._connection.execute(
            tasks.update().where(tasks.c.id == task_id).values(published=True)
        )


class PostgresTaskStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def lock_unpublished(self, grace: timedelta) -> AsyncIterator[PostgresUnpublishedBatch]:
        query = (
            sa.select(tasks.c.id)
            .where(tasks.c.published.is_(False), tasks.c.created_at < sa.func.now() - grace)
            .with_for_update(skip_locked=True)
        )
        # One transaction for the batch: its commit releases the row locks.
        async with self._engine.begin() as connection:
            task_ids = list((await connection.execute(query)).scalars())
            yield PostgresUnpublishedBatch(connection, task_ids)

    async def find_stale(self, stale_after: timedelta) -> list[StaleTask]:
        query = sa.select(
            tasks.c.id, (sa.func.now() - tasks.c.created_at).label("pending_for")
        ).where(tasks.c.status == TaskStatus.PENDING, tasks.c.created_at < sa.func.now() - stale_after)
        async with self._engine.connect() as connection:
            rows = await connection.execute(query)
        return [StaleTask.model_validate(row._mapping) for row in rows]
