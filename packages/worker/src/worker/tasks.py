"""The task handler: claims a pending task, simulates its circuit and records the outcome."""

import functools
import os
import uuid
from typing import assert_never

import sqlalchemy as sa
import structlog
from pydantic import BaseModel

from common.message import PROCESS_TASK_NAME
from common.tasks import ErrorCode, TaskStatus, tasks
from worker import runner
from worker.app import app
from worker.outcomes import SimulationCompleted, SimulationFailed
from worker.settings import SIMULATION_LIMITS

log = structlog.get_logger()


@functools.cache
def _engine() -> sa.Engine:
    return sa.create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


class ClaimedTask(BaseModel):
    circuit: str
    attempts: int


def _claim(task_id: uuid.UUID) -> ClaimedTask | None:
    """Count a new attempt and fetch the circuit, if the task is still pending.

    Returns None when the task is already final (or doesn't exist).
    """
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(attempts=tasks.c.attempts + 1, started_at=sa.func.now())
        .returning(tasks.c.circuit, tasks.c.attempts)
    )
    with _engine().begin() as connection:
        row = connection.execute(statement).one_or_none()
    return None if row is None else ClaimedTask.model_validate(row._mapping)


def _complete(task_id: uuid.UUID, counts: dict[str, int]) -> bool:
    """Record the counts, if the task is still pending; the first attempt to finish wins.

    Returns False when another attempt already recorded an outcome.
    """
    statement = (
        sa.update(tasks)
        .where(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)
        .values(status=TaskStatus.COMPLETED, result=counts, finished_at=sa.func.now())
    )
    with _engine().begin() as connection:
        return connection.execute(statement).rowcount == 1


def _fail(task_id: uuid.UUID, code: ErrorCode, message: str) -> bool:
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
    with _engine().begin() as connection:
        return connection.execute(statement).rowcount == 1


@app.task(name=PROCESS_TASK_NAME)  # type: ignore[untyped-decorator]
def process(task_id: str) -> None:
    task_log = log.bind(task_id=task_id)
    parsed_id = uuid.UUID(task_id)

    claimed = _claim(parsed_id)
    if claimed is None:
        task_log.info("task_skipped", reason="not pending")
        return
    task_log.info("task_started", attempt=claimed.attempts)

    outcome = runner.run(claimed.circuit, SIMULATION_LIMITS)

    match outcome:
        case SimulationCompleted(counts=counts):
            if _complete(parsed_id, counts):
                task_log.info("task_completed")
                return
        case SimulationFailed(code=code, message=message, traceback=traceback):
            if _fail(parsed_id, code, message):
                # A traceback means something raised: a fault to investigate, not just a bad
                # circuit. It crossed the process boundary as text, so it's logged as a field.
                log_failure = task_log.info if traceback is None else task_log.error
                log_failure("task_failed", error_code=code, message=message, traceback=traceback)
                return
        case _:
            assert_never(outcome)
    task_log.info("duplicate_result_discarded")
