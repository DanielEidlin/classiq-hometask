"""The worker's task store on Postgres.

Every call runs under the retry policy it is given (`worker.database.retry_while_unavailable`
in production), so while the database is unreachable the worker retries in place and takes no
new tasks. Once the outage outlasts the policy the call raises `DatabaseUnavailable`, for the
message to be returned to the queue rather than acknowledged.
"""

import functools
import uuid
from collections.abc import Callable
from typing import assert_never, cast

import sqlalchemy as sa
import structlog
import tenacity

from common.tasks import ErrorCode, TaskStatus, tasks
from worker.abstractions import DatabaseUnavailable, StartedAttempt
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

log = structlog.get_logger()

def _start_attempt(connection: sa.Connection, task_id: uuid.UUID) -> StartedAttempt | None:
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(attempts=tasks.c.attempts + 1, started_at=sa.func.now())
        .returning(tasks.c.circuit, tasks.c.attempts.label("number"))
    )
    row = connection.execute(statement).one_or_none()
    return None if row is None else StartedAttempt.model_validate(row._mapping)


def _fetch_status(connection: sa.Connection, task_id: uuid.UUID) -> TaskStatus | None:
    query = sa.select(tasks.c.status).where(tasks.c.id == task_id)
    status: TaskStatus | None = connection.execute(query).scalar_one_or_none()
    return status


def _complete(connection: sa.Connection, task_id: uuid.UUID, counts: dict[str, int]) -> bool:
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(status=TaskStatus.COMPLETED, result=counts, finished_at=sa.func.now())
    )
    return connection.execute(statement).rowcount == 1


def _fail(connection: sa.Connection, task_id: uuid.UUID, code: ErrorCode, message: str) -> bool:
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(
            status=TaskStatus.FAILED,
            error_code=code,
            error_message=message,
            finished_at=sa.func.now(),
        )
    )
    return connection.execute(statement).rowcount == 1


def _record_outcome(
    connection: sa.Connection, task_id: uuid.UUID, outcome: SimulationOutcome
) -> bool:
    match outcome:
        case SimulationCompleted(counts=counts):
            return _complete(connection, task_id, counts)
        case SimulationFailed(code=code, message=message):
            return _fail(connection, task_id, code, message)
        case _:
            assert_never(outcome)


def _log_failed_call(
    task_log: structlog.typing.FilteringBoundLogger, retry_state: tenacity.RetryCallState
) -> None:
    error = retry_state.outcome.exception() if retry_state.outcome is not None else None
    task_log.error("database_call_failed", failed_calls=retry_state.attempt_number, exc_info=error)


class PostgresTaskStore:
    def __init__(self, engine: sa.Engine, retrying: tenacity.Retrying) -> None:
        self._engine = engine
        self._retrying = retrying

    def start_attempt(self, task_id: uuid.UUID) -> StartedAttempt | None:
        return self._with_retries(task_id, functools.partial(_start_attempt, task_id=task_id))

    def fetch_status(self, task_id: uuid.UUID) -> TaskStatus | None:
        return self._with_retries(task_id, functools.partial(_fetch_status, task_id=task_id))

    def record_outcome(self, task_id: uuid.UUID, outcome: SimulationOutcome) -> bool:
        return self._with_retries(
            task_id, functools.partial(_record_outcome, task_id=task_id, outcome=outcome)
        )

    def _in_transaction[T](self, operation: Callable[[sa.Connection], T]) -> T:
        with self._engine.begin() as connection:
            return operation(connection)

    def _with_retries[T](self, task_id: uuid.UUID, operation: Callable[[sa.Connection], T]) -> T:
        """Run `operation` in a transaction, retrying while the database is unreachable."""
        task_log = log.bind(task_id=str(task_id))
        retrying = self._retrying.copy(after=functools.partial(_log_failed_call, task_log))
        try:
            return cast(T, retrying(self._in_transaction, operation))
        except tenacity.RetryError as error:
            task_log.error("database_unavailable", failed_calls=error.last_attempt.attempt_number)
            raise DatabaseUnavailable from None
