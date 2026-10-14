"""The task handler: claims a pending task, simulates its circuit and records the outcome.

It has no Celery dependency; `worker.tasks` binds it to the queue.

A task may be delivered more than once: Celery acknowledges a message only after the task
finishes, so RabbitMQ redelivers it when a worker dies. Every write therefore applies only
while the task is pending, and the first outcome recorded wins.

If the database can't take the outcome, the handler retries the write in place, so the worker
takes no new tasks meanwhile. Once the outage outlasts the retry policy it gives up and raises
`RecordingAbandoned`, for the message to be returned to the queue rather than acknowledged.
"""

import functools
import time
import uuid
from collections.abc import Callable
from typing import Self, assert_never

import sqlalchemy as sa
import structlog
from pydantic import BaseModel

from common.tasks import ErrorCode, TaskStatus, tasks
from worker import runner
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.retry import RetryPolicy, backoff_delays
from worker.settings import DB_TIMEOUT_SECONDS, MAX_ATTEMPTS, RECORD_RETRY_POLICY, SIMULATION_LIMITS

log = structlog.get_logger()

WORKER_LOST_MESSAGE = (
    "The worker running the task was lost {max_attempts} times, so it was not run again."
)

Simulation = Callable[[str], SimulationOutcome]


class RecordingAbandoned(Exception):
    """The database stayed unreachable for the whole retry policy; the outcome wasn't recorded."""


def create_db_engine(database_url: str, timeout_seconds: float) -> sa.Engine:
    """An engine whose every database call fails within about `timeout_seconds` rather than hang."""
    timeout_ms = int(timeout_seconds * 1000)
    return sa.create_engine(
        database_url,
        pool_pre_ping=True,
        pool_timeout=timeout_seconds,
        connect_args={
            # libpq takes whole seconds, and treats 0 as no timeout.
            "connect_timeout": max(1, round(timeout_seconds)),
            # statement_timeout bounds a slow query; tcp_user_timeout bounds a peer that
            # stopped answering mid-query, which the server-side timeout can't see.
            "options": f"-c statement_timeout={timeout_ms}",
            "tcp_user_timeout": timeout_ms,
        },
    )


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


class TaskHandler:
    def __init__(
        self,
        engine: sa.Engine,
        simulation: Simulation,
        max_attempts: int,
        record_retry_policy: RetryPolicy,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._simulation = simulation
        self._max_attempts = max_attempts
        self._record_retry_policy = record_retry_policy
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def connect(cls, database_url: str) -> Self:
        return cls(
            create_db_engine(database_url, DB_TIMEOUT_SECONDS),
            functools.partial(runner.run, limits=SIMULATION_LIMITS),
            MAX_ATTEMPTS,
            RECORD_RETRY_POLICY,
        )

    def _record_with_retries(
        self,
        task_log: structlog.typing.FilteringBoundLogger,
        task_id: uuid.UUID,
        outcome: SimulationOutcome,
    ) -> bool:
        policy = self._record_retry_policy
        deadline = self._clock() + policy.give_up_seconds
        delays = backoff_delays(policy.first_delay_seconds, policy.max_delay_seconds)
        write_attempt = 0
        while True:
            write_attempt += 1
            try:
                with self._engine.begin() as connection:
                    return _record(connection, task_id, outcome)
            except sa.exc.OperationalError:
                task_log.error("outcome_write_failed", write_attempt=write_attempt, exc_info=True)
            remaining = deadline - self._clock()
            if remaining <= 0:
                task_log.error(
                    "outcome_write_abandoned",
                    write_attempts=write_attempt,
                    give_up_seconds=policy.give_up_seconds,
                )
                raise RecordingAbandoned
            self._sleep(min(next(delays), remaining))

    def handle(self, task_id: uuid.UUID) -> None:
        task_log = log.bind(task_id=str(task_id))

        with self._engine.begin() as connection:
            claimed = _claim(connection, task_id)
        if claimed is None:
            # A separate read: the conditional claim can't tell a final task from a missing one.
            with self._engine.connect() as connection:
                status = _fetch_status(connection, task_id)
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
            outcome = self._simulation(claimed.circuit)

        if self._record_with_retries(task_log, task_id, outcome):
            _log_recorded(task_log, outcome)
        else:
            task_log.info("duplicate_result_discarded")
