"""The task handler: claims a pending task, simulates its circuit and records the outcome.

It has no Celery dependency; `worker.tasks` binds it to the queue.

A task may be delivered more than once: Celery acknowledges a message only after the task
finishes, so RabbitMQ redelivers it when a worker dies. Every write therefore applies only
while the task is pending, and the first outcome recorded wins.
"""

import functools
import uuid
from collections.abc import Callable
from typing import Self, assert_never

import sqlalchemy as sa
import structlog
from pydantic import BaseModel

from common.tasks import ErrorCode, TaskStatus, tasks
from worker import runner
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.settings import MAX_ATTEMPTS, SIMULATION_LIMITS

log = structlog.get_logger()

WORKER_LOST_MESSAGE = (
    "The worker running the task was lost {max_attempts} times, so it was not run again."
)

Simulation = Callable[[str], SimulationOutcome]


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
    def __init__(self, engine: sa.Engine, simulation: Simulation, max_attempts: int) -> None:
        self._engine = engine
        self._simulation = simulation
        self._max_attempts = max_attempts

    @classmethod
    def connect(cls, database_url: str) -> Self:
        engine = sa.create_engine(database_url, pool_pre_ping=True)
        return cls(engine, functools.partial(runner.run, limits=SIMULATION_LIMITS), MAX_ATTEMPTS)

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

        with self._engine.begin() as connection:
            recorded = _record(connection, task_id, outcome)
        if recorded:
            _log_recorded(task_log, outcome)
        else:
            task_log.info("duplicate_result_discarded")
