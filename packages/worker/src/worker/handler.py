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
            task_log.info("task_skipped", reason="not pending")
            return
        task_log.info("task_started", attempt=claimed.attempts)

        if claimed.attempts > self._max_attempts:
            message = WORKER_LOST_MESSAGE.format(max_attempts=self._max_attempts)
            with self._engine.begin() as connection:
                recorded = _fail(connection, task_id, ErrorCode.WORKER_LOST, message)
            if recorded:
                # Workers keep dying on this task: likely a fault to investigate.
                task_log.warning("task_failed", error_code=ErrorCode.WORKER_LOST, message=message)
                return
            task_log.info("duplicate_result_discarded")
            return

        outcome = self._simulation(claimed.circuit)

        match outcome:
            case SimulationCompleted(counts=counts):
                with self._engine.begin() as connection:
                    recorded = _complete(connection, task_id, counts)
                if recorded:
                    task_log.info("task_completed")
                    return
            case SimulationFailed(code=code, message=message, traceback=traceback):
                with self._engine.begin() as connection:
                    recorded = _fail(connection, task_id, code, message)
                if recorded:
                    # A traceback means something raised: a fault to investigate, not just a
                    # bad circuit. It crossed the process boundary as text, so it's logged as
                    # a field.
                    log_failure = task_log.info if traceback is None else task_log.error
                    log_failure(
                        "task_failed", error_code=code, message=message, traceback=traceback
                    )
                    return
            case _:
                assert_never(outcome)
        task_log.info("duplicate_result_discarded")
