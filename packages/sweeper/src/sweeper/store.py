"""The sweeper's task store on Postgres."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from common.tasks import TaskStatus, tasks
from sweeper.abstractions import StaleTask


async def _lock_unpublished(connection: AsyncConnection, grace: timedelta) -> list[uuid.UUID]:
    query = (
        sa.select(tasks.c.id)
        .where(tasks.c.published.is_(False), tasks.c.created_at < sa.func.now() - grace)
        .with_for_update(skip_locked=True)
    )
    return list((await connection.execute(query)).scalars())


async def _mark_published(connection: AsyncConnection, task_id: uuid.UUID) -> None:
    await connection.execute(tasks.update().where(tasks.c.id == task_id).values(published=True))


async def _find_stale(connection: AsyncConnection, stale_after: timedelta) -> list[StaleTask]:
    query = sa.select(tasks.c.id, (sa.func.now() - tasks.c.created_at).label("pending_for")).where(
        tasks.c.status == TaskStatus.PENDING, tasks.c.created_at < sa.func.now() - stale_after
    )
    rows = await connection.execute(query)
    return [StaleTask.model_validate(row._mapping) for row in rows]


class PostgresUnpublishedBatch:
    def __init__(self, connection: AsyncConnection, task_ids: list[uuid.UUID]) -> None:
        self._connection = connection
        self._task_ids = task_ids

    @property
    def task_ids(self) -> list[uuid.UUID]:
        return self._task_ids

    async def mark_published(self, task_id: uuid.UUID) -> None:
        await _mark_published(self._connection, task_id)


class PostgresTaskStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def lock_unpublished(self, grace: timedelta) -> AsyncIterator[PostgresUnpublishedBatch]:
        # One transaction for the batch: its commit releases the row locks.
        async with self._engine.begin() as connection:
            yield PostgresUnpublishedBatch(connection, await _lock_unpublished(connection, grace))

    async def find_stale(self, stale_after: timedelta) -> list[StaleTask]:
        async with self._engine.connect() as connection:
            return await _find_stale(connection, stale_after)
