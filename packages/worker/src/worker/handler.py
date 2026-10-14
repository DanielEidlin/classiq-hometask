"""The task handler: claims a pending task, simulates its circuit and records the outcome.

It has no Celery dependency; `worker.tasks` binds it to the queue.

A task may be delivered more than once: Celery acknowledges a message only after the task
finishes, so RabbitMQ redelivers it when a worker dies. Every write therefore applies only
while the task is pending, and the first outcome recorded wins.

If the database is unreachable, the handler retries the call in place, so the worker takes no
new tasks meanwhile. Once the outage outlasts the retry policy it gives up and raises
`DatabaseUnavailable`, for the message to be returned to the queue rather than acknowledged.
A requeue after the claim committed has used up an attempt, so an outage that outlasts the
policy MAX_ATTEMPTS times in a row fails the task as WORKER_LOST.
"""

import functools
import uuid
from collections.abc import Callable
from typing import Protocol, Self, assert_never, cast

import sqlalchemy as sa
import structlog
import tenacity
from pydantic import BaseModel

from common.tasks import ErrorCode, TaskStatus, tasks
from worker.database import create_db_engine, retry_while_unavailable
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.runner import SimulationRunner
from worker.settings import (
    DB_OUTAGE_GIVE_UP_SECONDS,
    DB_RETRY_FIRST_DELAY_SECONDS,
    DB_RETRY_MAX_DELAY_SECONDS,
    DB_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    SIMULATION_LIMITS,
)

log = structlog.get_logger()

WORKER_LOST_MESSAGE = (
    "The worker running the task was lost {max_attempts} times, so it was not run again."
)


class CircuitRunner(Protocol):
    def run(self, circuit: str) -> SimulationOutcome: ...


class DatabaseUnavailable(Exception):
    """The database stayed unreachable for the whole retry policy."""


class ClaimedTask(BaseModel):
    circuit: str
    attempts: int


def _claim(connection: sa.Connection, task_id: uuid.UUID) -> ClaimedTask | None:
    """Count a new attempt and fetch the circuit, if the task is still pending.

    Returns None when the task is already final (or doesn't exist).
    """
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(attempts=tasks.c.attempts + 1, started_at=sa.func.now())
        .returning(tasks.c.circuit, tasks.c.attempts)
    )
    row = connection.execute(statement).one_or_none()
    return None if row is None else ClaimedTask.model_validate(row._mapping)


def _fetch_status(connection: sa.Connection, task_id: uuid.UUID) -> TaskStatus | None:
    """The task's status, or None when it doesn't exist."""
    query = sa.select(tasks.c.status).where(tasks.c.id == task_id)
    status: TaskStatus | None = connection.execute(query).scalar_one_or_none()
    return status


def _complete(connection: sa.Connection, task_id: uuid.UUID, counts: dict[str, int]) -> bool:
    """Record the counts, if the task is still pending; the first attempt to finish wins.

    Returns False when another attempt already recorded an outcome.
    """
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(status=TaskStatus.COMPLETED, result=counts, finished_at=sa.func.now())
    )
    return connection.execute(statement).rowcount == 1


def _fail(connection: sa.Connection, task_id: uuid.UUID, code: ErrorCode, message: str) -> bool:
    """Record the failure, if the task is still pending; the first attempt to finish wins.

    Returns False when another attempt already recorded an outcome.
    """
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


def _record(connection: sa.Connection, task_id: uuid.UUID, outcome: SimulationOutcome) -> bool:
    match outcome:
        case SimulationCompleted(counts=counts):
            return _complete(connection, task_id, counts)
        case SimulationFailed(code=code, message=message):
            return _fail(connection, task_id, code, message)
        case _:
            assert_never(outcome)


def _log_recorded(task_log: structlog.typing.FilteringBoundLogger, outcome: SimulationOutcome) -> None:
    match outcome:
        case SimulationCompleted():
            task_log.info("task_completed")
        case SimulationFailed(code=code, message=message, traceback=traceback):
            # A traceback means something raised, and workers lost on every attempt suggest a
            # circuit that crashes them: faults to investigate, not just a bad circuit. The
            # traceback crossed the process boundary as text, so it's logged as a field.
            if traceback is not None:
                log_failure = task_log.error
            elif code == ErrorCode.WORKER_LOST:
                log_failure = task_log.warning
            else:
                log_failure = task_log.info
            log_failure("task_failed", error_code=code, message=message, traceback=traceback)
        case _:
            assert_never(outcome)


def _log_failed_call(
    task_log: structlog.typing.FilteringBoundLogger, retry_state: tenacity.RetryCallState
) -> None:
    error = retry_state.outcome.exception() if retry_state.outcome is not None else None
    task_log.error("database_call_failed", failed_calls=retry_state.attempt_number, exc_info=error)


class TaskHandler:
    def __init__(
        self,
        engine: sa.Engine,
        circuit_runner: CircuitRunner,
        max_attempts: int,
        retrying: tenacity.Retrying,
    ) -> None:
        self._engine = engine
        self._circuit_runner = circuit_runner
        self._max_attempts = max_attempts
        self._retrying = retrying

    @classmethod
    def connect(cls, database_url: str) -> Self:
        return cls(
            create_db_engine(database_url, DB_TIMEOUT_SECONDS),
            SimulationRunner(SIMULATION_LIMITS),
            MAX_ATTEMPTS,
            retry_while_unavailable(
                DB_RETRY_FIRST_DELAY_SECONDS, DB_RETRY_MAX_DELAY_SECONDS, DB_OUTAGE_GIVE_UP_SECONDS
            ),
        )

    def _in_transaction[T](self, operation: Callable[[sa.Connection], T]) -> T:
        with self._engine.begin() as connection:
            return operation(connection)

    def _with_retries[T](
        self,
        task_log: structlog.typing.FilteringBoundLogger,
        operation: Callable[[sa.Connection], T],
    ) -> T:
        """Run `operation` in a transaction, retrying while the database is unreachable."""
        retrying = self._retrying.copy(after=functools.partial(_log_failed_call, task_log))
        try:
            return cast(T, retrying(self._in_transaction, operation))
        except tenacity.RetryError as error:
            task_log.error("database_unavailable", failed_calls=error.last_attempt.attempt_number)
            raise DatabaseUnavailable from None

    def handle(self, task_id: uuid.UUID) -> None:
        task_log = log.bind(task_id=str(task_id))

        claimed = self._with_retries(task_log, functools.partial(_claim, task_id=task_id))
        if claimed is None:
            # A separate read: the conditional claim can't tell a final task from a missing one.
            status = self._with_retries(task_log, functools.partial(_fetch_status, task_id=task_id))
            if status is None:
                task_log.info("task_skipped", reason="not found")
            else:
                task_log.info("task_skipped", reason="already final", status=status)
            return
        task_log.info("task_started", attempt=claimed.attempts)

        outcome: SimulationOutcome
        if claimed.attempts > self._max_attempts:
            outcome = SimulationFailed(
                code=ErrorCode.WORKER_LOST,
                message=WORKER_LOST_MESSAGE.format(max_attempts=self._max_attempts),
            )
        else:
            outcome = self._circuit_runner.run(claimed.circuit)

        recorded = self._with_retries(
            task_log, functools.partial(_record, task_id=task_id, outcome=outcome)
        )
        if recorded:
            _log_recorded(task_log, outcome)
        else:
            task_log.info("duplicate_result_discarded")
