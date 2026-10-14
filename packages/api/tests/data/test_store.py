"""The API's task store against a real Postgres."""

import uuid

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from api.data.store import PostgresTaskStore
from api.domain.abstractions import DatabaseUnavailable
from api.domain.outcomes import CompletedOutcome, FailedOutcome, PendingOutcome
from common.data.tables import tasks
from common.domain.tasks import ErrorCode, TaskStatus

pytestmark = pytest.mark.asyncio

CIRCUIT = "OPENQASM 3.0;"
COUNTS = {"00": 512, "11": 512}
FAILURE_MESSAGE = "Simulation exceeded the time limit of 60 seconds."
# Nothing listens there. Some hosts drop the connection attempt rather than refuse it, so
# connecting is also given a short timeout.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://tasks:tasks@127.0.0.1:1/tasks"
CONNECT_TIMEOUT_SECONDS = 1


async def _fetch_row(engine: AsyncEngine, task_id: uuid.UUID) -> sa.RowMapping | None:
    async with engine.connect() as connection:
        query = sa.select(tasks).where(tasks.c.id == task_id)
        return (await connection.execute(query)).mappings().one_or_none()


async def _finish(engine: AsyncEngine, task_id: uuid.UUID, **values: object) -> None:
    async with engine.begin() as connection:
        await connection.execute(tasks.update().where(tasks.c.id == task_id).values(**values))


async def test_an_inserted_task_is_pending_and_unpublished(engine: AsyncEngine) -> None:
    task_id = uuid.uuid4()
    store = PostgresTaskStore(engine)

    await store.insert_task(task_id, CIRCUIT)

    assert await store.fetch_outcome(task_id) == PendingOutcome(status=TaskStatus.PENDING)
    row = await _fetch_row(engine, task_id)
    assert row is not None
    assert (row["circuit"], row["published"]) == (CIRCUIT, False)


async def test_a_task_marked_published_is_published(engine: AsyncEngine) -> None:
    task_id = uuid.uuid4()
    store = PostgresTaskStore(engine)
    await store.insert_task(task_id, CIRCUIT)

    await store.mark_published(task_id)

    row = await _fetch_row(engine, task_id)
    assert row is not None
    assert row["published"]


async def test_a_deleted_task_is_gone(engine: AsyncEngine) -> None:
    task_id = uuid.uuid4()
    store = PostgresTaskStore(engine)
    await store.insert_task(task_id, CIRCUIT)

    await store.delete_task(task_id)

    assert await store.fetch_outcome(task_id) is None


async def test_a_completed_task_is_read_with_its_counts(engine: AsyncEngine) -> None:
    task_id = uuid.uuid4()
    store = PostgresTaskStore(engine)
    await store.insert_task(task_id, CIRCUIT)
    await _finish(engine, task_id, status=TaskStatus.COMPLETED, result=COUNTS)

    assert await store.fetch_outcome(task_id) == CompletedOutcome(
        status=TaskStatus.COMPLETED, result=COUNTS
    )


async def test_a_failed_task_is_read_with_its_message(engine: AsyncEngine) -> None:
    task_id = uuid.uuid4()
    store = PostgresTaskStore(engine)
    await store.insert_task(task_id, CIRCUIT)
    await _finish(
        engine,
        task_id,
        status=TaskStatus.FAILED,
        error_code=ErrorCode.TIMEOUT,
        error_message=FAILURE_MESSAGE,
    )

    assert await store.fetch_outcome(task_id) == FailedOutcome(
        status=TaskStatus.FAILED, error_message=FAILURE_MESSAGE
    )


async def test_a_completed_task_without_counts_is_rejected(engine: AsyncEngine) -> None:
    task_id = uuid.uuid4()
    store = PostgresTaskStore(engine)
    await store.insert_task(task_id, CIRCUIT)
    await _finish(engine, task_id, status=TaskStatus.COMPLETED)

    with pytest.raises(ValidationError):
        await store.fetch_outcome(task_id)


async def test_a_missing_task_has_no_outcome(engine: AsyncEngine) -> None:
    assert await PostgresTaskStore(engine).fetch_outcome(uuid.uuid4()) is None


async def test_reachability_reflects_whether_postgres_answers(engine: AsyncEngine) -> None:
    unreachable = create_async_engine(
        UNREACHABLE_DATABASE_URL, connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    )
    try:
        assert await PostgresTaskStore(engine).is_reachable()
        assert not await PostgresTaskStore(unreachable).is_reachable()
    finally:
        await unreachable.dispose()


async def test_calls_raise_database_unavailable_while_postgres_is_unreachable() -> None:
    unreachable = create_async_engine(
        UNREACHABLE_DATABASE_URL, connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    )
    store = PostgresTaskStore(unreachable)
    try:
        with pytest.raises(DatabaseUnavailable):
            await store.insert_task(uuid.uuid4(), CIRCUIT)
        with pytest.raises(DatabaseUnavailable):
            await store.fetch_outcome(uuid.uuid4())
    finally:
        await unreachable.dispose()
