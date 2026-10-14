"""One sweeper pass, with its task store and publisher replaced."""

import functools
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, call

import pytest
import structlog
from aio_pika.exceptions import DeliveryError
from pamqp.commands import Basic

from fakes.sweeper_task_store import FakeTaskStore
from sweeper.business.sweeper import Sweeper
from sweeper.domain.abstractions import StaleTask

pytestmark = pytest.mark.asyncio

GRACE = timedelta(seconds=30)
STALE_AFTER = timedelta(minutes=10)
PENDING_FOR = timedelta(minutes=25)


@pytest.fixture
def task_store() -> FakeTaskStore:
    return FakeTaskStore()


@pytest.fixture
def sweeper(task_store: FakeTaskStore, publisher: AsyncMock) -> Sweeper:
    return Sweeper(task_store, publisher, grace=GRACE, stale_after=STALE_AFTER)


async def test_each_unpublished_task_is_published_then_marked(
    sweeper: Sweeper, task_store: FakeTaskStore, publisher: AsyncMock
) -> None:
    task_store.unpublished = [uuid.uuid4(), uuid.uuid4()]

    await sweeper.sweep()

    assert publisher.publish.await_args_list == [call(task_id) for task_id in task_store.unpublished]
    assert task_store.marked_published == task_store.unpublished


async def _reject_only(rejected_id: uuid.UUID, task_id: uuid.UUID) -> None:
    if task_id == rejected_id:
        raise DeliveryError(None, Basic.Nack())


async def test_a_rejected_publish_leaves_its_task_unmarked_and_the_pass_goes_on(
    sweeper: Sweeper, task_store: FakeTaskStore, publisher: AsyncMock
) -> None:
    rejected_id, accepted_id = uuid.uuid4(), uuid.uuid4()
    task_store.unpublished = [rejected_id, accepted_id]
    publisher.publish.side_effect = functools.partial(_reject_only, rejected_id)

    with structlog.testing.capture_logs() as logs:
        await sweeper.sweep()

    assert task_store.marked_published == [accepted_id]
    failures = [entry for entry in logs if entry["event"] == "publish_failed"]
    assert [entry["task_id"] for entry in failures] == [str(rejected_id)]
    assert failures[0]["log_level"] == "warning"


async def test_stale_task_is_logged_and_not_republished(
    sweeper: Sweeper, task_store: FakeTaskStore, publisher: AsyncMock
) -> None:
    stale_id = uuid.uuid4()
    task_store.stale = [StaleTask(id=stale_id, pending_for=PENDING_FOR)]

    with structlog.testing.capture_logs() as logs:
        await sweeper.sweep()

    assert {
        "event": "stale_task",
        "task_id": str(stale_id),
        "pending_for_seconds": int(PENDING_FOR.total_seconds()),
        "log_level": "warning",
    } in logs
    publisher.publish.assert_not_awaited()


async def _record_batch_open(
    task_store: FakeTaskStore, batch_open_at_each_publish: list[bool], task_id: uuid.UUID
) -> None:
    batch_open_at_each_publish.append(task_store.batch_open)


async def test_tasks_are_published_while_their_batch_still_holds_them(
    sweeper: Sweeper, task_store: FakeTaskStore, publisher: AsyncMock
) -> None:
    """The batch's locks keep other sweepers off these tasks only while it is open."""
    task_store.unpublished = [uuid.uuid4(), uuid.uuid4()]
    batch_open_at_each_publish: list[bool] = []
    publisher.publish.side_effect = functools.partial(
        _record_batch_open, task_store, batch_open_at_each_publish
    )

    await sweeper.sweep()

    assert batch_open_at_each_publish == [True, True]
    assert not task_store.batch_open
