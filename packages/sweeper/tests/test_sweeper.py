import asyncio
import functools
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, call

import pytest
import sqlalchemy as sa
import structlog
from aio_pika.exceptions import DeliveryError
from pamqp.commands import Basic
from sqlalchemy.ext.asyncio import AsyncEngine

from common.tasks import TaskStatus, tasks
from sweeper.sweeper import Sweeper

pytestmark = pytest.mark.asyncio

GRACE = timedelta(seconds=30)
STALE_AFTER = timedelta(minutes=10)
TASK_COUNT = 5
PUBLISH_DELAY_SECONDS = 0.05


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


async def _is_published(engine: AsyncEngine, task_id: uuid.UUID) -> bool:
    async with engine.connect() as connection:
        query = sa.select(tasks.c.published).where(tasks.c.id == task_id)
        published: bool = (await connection.execute(query)).scalar_one()
    return published


def _sweeper(engine: AsyncEngine, publisher: AsyncMock) -> Sweeper:
    return Sweeper(engine, publisher, grace=GRACE, stale_after=STALE_AFTER)


async def test_unpublished_task_older_than_the_grace_period_is_published(
    engine: AsyncEngine, publisher: AsyncMock
) -> None:
    task_id = await _insert_task(engine, age=GRACE * 2)

    await _sweeper(engine, publisher).sweep()

    assert publisher.publish.await_args_list == [call(task_id)]
    assert await _is_published(engine, task_id)


async def test_task_younger_than_the_grace_period_is_not_touched(
    engine: AsyncEngine, publisher: AsyncMock
) -> None:
    task_id = await _insert_task(engine, age=GRACE / 2)

    await _sweeper(engine, publisher).sweep()

    publisher.publish.assert_not_awaited()
    assert not await _is_published(engine, task_id)


async def _slow_publish(task_id: uuid.UUID) -> None:
    await asyncio.sleep(PUBLISH_DELAY_SECONDS)


async def test_two_sweepers_at_once_publish_each_task_once(
    engine: AsyncEngine, publisher: AsyncMock
) -> None:
    task_ids = {await _insert_task(engine, age=GRACE * 2) for _ in range(TASK_COUNT)}
    # A slow publish keeps the first pass's transaction open while the second one runs.
    publisher.publish.side_effect = _slow_publish

    await asyncio.gather(
        _sweeper(engine, publisher).sweep(), _sweeper(engine, publisher).sweep()
    )

    published_ids = [published.args[0] for published in publisher.publish.await_args_list]
    assert sorted(published_ids) == sorted(task_ids)


async def test_rejected_publish_leaves_the_task_for_the_next_pass(
    engine: AsyncEngine, publisher: AsyncMock
) -> None:
    rejected_id = await _insert_task(engine, age=GRACE * 2)
    accepted_id = await _insert_task(engine, age=GRACE * 2)
    publisher.publish.side_effect = functools.partial(_reject_only, rejected_id)

    await _sweeper(engine, publisher).sweep()

    assert not await _is_published(engine, rejected_id)
    assert await _is_published(engine, accepted_id)

    publisher.publish.side_effect = None
    await _sweeper(engine, publisher).sweep()

    assert await _is_published(engine, rejected_id)


async def _reject_only(rejected_id: uuid.UUID, task_id: uuid.UUID) -> None:
    if task_id == rejected_id:
        raise DeliveryError(None, Basic.Nack())


async def test_stale_pending_task_is_logged_and_not_republished(
    engine: AsyncEngine, publisher: AsyncMock
) -> None:
    stale_id = await _insert_task(engine, age=STALE_AFTER * 2, published=True)
    await _insert_task(engine, age=STALE_AFTER * 2, published=True, status=TaskStatus.COMPLETED)
    await _insert_task(engine, age=STALE_AFTER / 2, published=True)

    with structlog.testing.capture_logs() as logs:
        await _sweeper(engine, publisher).sweep()

    stale_warnings = [entry for entry in logs if entry["event"] == "stale_task"]
    assert [entry["task_id"] for entry in stale_warnings] == [str(stale_id)]
    assert stale_warnings[0]["log_level"] == "warning"
    publisher.publish.assert_not_awaited()
