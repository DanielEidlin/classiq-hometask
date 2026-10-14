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
from sweeper.abstractions import StaleTask
from sweeper.sweeper import Sweeper

pytestmark = pytest.mark.asyncio

GRACE = timedelta(seconds=30)
STALE_AFTER = timedelta(minutes=10)
PENDING_FOR = timedelta(minutes=25)


def _build_sweeper(store: FakeTaskStore, publisher: AsyncMock) -> Sweeper:
    return Sweeper(store, publisher, grace=GRACE, stale_after=STALE_AFTER)


async def test_each_unpublished_task_is_published_then_marked(publisher: AsyncMock) -> None:
    task_ids = [uuid.uuid4(), uuid.uuid4()]
    store = FakeTaskStore(unpublished=task_ids)

    await _build_sweeper(store, publisher).sweep()

    assert publisher.publish.await_args_list == [call(task_id) for task_id in task_ids]
    assert store.marked_published == task_ids


async def _reject_only(rejected_id: uuid.UUID, task_id: uuid.UUID) -> None:
    if task_id == rejected_id:
        raise DeliveryError(None, Basic.Nack())


async def test_a_rejected_publish_leaves_its_task_unmarked_and_the_pass_goes_on(
    publisher: AsyncMock,
) -> None:
    rejected_id, accepted_id = uuid.uuid4(), uuid.uuid4()
    store = FakeTaskStore(unpublished=[rejected_id, accepted_id])
    publisher.publish.side_effect = functools.partial(_reject_only, rejected_id)

    with structlog.testing.capture_logs() as logs:
        await _build_sweeper(store, publisher).sweep()

    assert store.marked_published == [accepted_id]
    failures = [entry for entry in logs if entry["event"] == "publish_failed"]
    assert [entry["task_id"] for entry in failures] == [str(rejected_id)]
    assert failures[0]["log_level"] == "warning"


async def test_stale_task_is_logged_and_not_republished(publisher: AsyncMock) -> None:
    stale_id = uuid.uuid4()
    store = FakeTaskStore(stale=[StaleTask(id=stale_id, pending_for=PENDING_FOR)])

    with structlog.testing.capture_logs() as logs:
        await _build_sweeper(store, publisher).sweep()

    assert {
        "event": "stale_task",
        "task_id": str(stale_id),
        "pending_for_seconds": int(PENDING_FOR.total_seconds()),
        "log_level": "warning",
    } in logs
    publisher.publish.assert_not_awaited()
