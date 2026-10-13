"""One pass of the sweeper: publish tasks the API stored but never published, and report
tasks that have been pending too long."""

import uuid
from datetime import timedelta

import sqlalchemy as sa
import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from common.publisher import TaskPublisher
from common.tasks import TaskStatus, tasks

log = structlog.get_logger()


async def _lock_unpublished(connection: AsyncConnection, grace: timedelta) -> list[uuid.UUID]:
    """Lock the unpublished tasks older than `grace`, skipping any another sweeper holds."""
    query = (
        sa.select(tasks.c.id)
        .where(tasks.c.published.is_(False), tasks.c.created_at < sa.func.now() - grace)
        .with_for_update(skip_locked=True)
    )
    return list((await connection.execute(query)).scalars())


async def _mark_published(connection: AsyncConnection, task_id: uuid.UUID) -> None:
    await connection.execute(tasks.update().where(tasks.c.id == task_id).values(published=True))


class StaleTask(BaseModel):
    id: uuid.UUID
    pending_for: timedelta


async def _find_stale(connection: AsyncConnection, stale_after: timedelta) -> list[StaleTask]:
    query = sa.select(tasks.c.id, (sa.func.now() - tasks.c.created_at).label("pending_for")).where(
        tasks.c.status == TaskStatus.PENDING, tasks.c.created_at < sa.func.now() - stale_after
    )
    rows = await connection.execute(query)
    return [StaleTask.model_validate(row._mapping) for row in rows]


class Sweeper:
    def __init__(
        self, engine: AsyncEngine, publisher: TaskPublisher, grace: timedelta, stale_after: timedelta
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._grace = grace
        self._stale_after = stale_after

    async def sweep(self) -> None:
        await self._publish_unpublished()
        await self._report_stale()

    async def _publish_unpublished(self) -> None:
        # The row locks are held until every locked task has been published, so a second
        # sweeper skips these tasks instead of publishing them again.
        async with self._engine.begin() as connection:
            for task_id in await _lock_unpublished(connection, self._grace):
                try:
                    await self._publisher.publish(task_id)
                except Exception:
                    # Rejected (queue full) or the broker is unreachable: the task stays
                    # unpublished, and the next pass tries again.
                    log.warning("publish_failed", task_id=str(task_id), exc_info=True)
                    continue
                await _mark_published(connection, task_id)
                log.info("task_published", task_id=str(task_id))

    async def _report_stale(self) -> None:
        """Warn about tasks pending too long. They are not republished: a task legitimately
        waiting in a long queue looks the same as one whose message was lost (decision 18)."""
        async with self._engine.connect() as connection:
            stale_tasks = await _find_stale(connection, self._stale_after)
        for stale in stale_tasks:
            log.warning(
                "stale_task",
                task_id=str(stale.id),
                pending_for_seconds=int(stale.pending_for.total_seconds()),
            )
