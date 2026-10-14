"""The sweeper's task store against a real Postgres."""

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from common.data.tables import tasks
from common.domain.tasks import TaskStatus
from sweeper.data.store import PostgresTaskStore

pytestmark = pytest.mark.asyncio

GRACE = timedelta(seconds=30)
STALE_AFTER = timedelta(minutes=10)


async def _insert_task(
    engine: AsyncEngine,
    *,
    age: timedelta,
    published: bool = False,
    status: TaskStatus = TaskStatus.PENDING,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            tasks.insert().values(
                id=task_id,
                circuit="OPENQASM 3.0;",
                status=status,
                published=published,
                created_at=sa.func.now() - age,
            )
        )
    return task_id


async def test_only_unpublished_tasks_older_than_the_grace_period_are_locked(
    engine: AsyncEngine,
) -> None:
    old_id = await _insert_task(engine, age=GRACE * 2)
    await _insert_task(engine, age=GRACE / 2)
    await _insert_task(engine, age=GRACE * 2, published=True)

    async with PostgresTaskStore(engine).lock_unpublished(GRACE) as batch:
        assert batch.task_ids == [old_id]


async def test_tasks_in_an_open_batch_are_skipped_by_another(engine: AsyncEngine) -> None:
    """As two sweepers at once: the second can't take what the first holds."""
    task_id = await _insert_task(engine, age=GRACE * 2)
    store = PostgresTaskStore(engine)

    async with store.lock_unpublished(GRACE) as first:
        async with store.lock_unpublished(GRACE) as second:
            assert (first.task_ids, second.task_ids) == ([task_id], [])


async def test_only_tasks_marked_in_the_batch_are_published_once_it_ends(
    engine: AsyncEngine,
) -> None:
    marked_id = await _insert_task(engine, age=GRACE * 2)
    unmarked_id = await _insert_task(engine, age=GRACE * 2)

    store = PostgresTaskStore(engine)
    async with store.lock_unpublished(GRACE) as batch:
        await batch.mark_published(marked_id)

    async with store.lock_unpublished(GRACE) as next_batch:
        assert next_batch.task_ids == [unmarked_id]


async def test_only_tasks_pending_longer_than_the_limit_are_stale(engine: AsyncEngine) -> None:
    stale_id = await _insert_task(engine, age=STALE_AFTER * 2, published=True)
    await _insert_task(engine, age=STALE_AFTER * 2, published=True, status=TaskStatus.COMPLETED)
    await _insert_task(engine, age=STALE_AFTER / 2, published=True)

    stale_tasks = await PostgresTaskStore(engine).find_stale(STALE_AFTER)

    assert [stale.id for stale in stale_tasks] == [stale_id]
    assert stale_tasks[0].pending_for > STALE_AFTER
