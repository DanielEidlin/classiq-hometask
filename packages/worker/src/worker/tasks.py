"""The task handler: claims a pending task, simulates its circuit and records the counts."""

import functools
import os
import uuid

import sqlalchemy as sa
import structlog
from pydantic import BaseModel

from common.message import PROCESS_TASK_NAME
from common.tasks import TaskStatus, tasks
from worker import simulation
from worker.app import app

log = structlog.get_logger()


SHOTS = int(os.environ.get("SHOTS", "1024"))


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


@app.task(name=PROCESS_TASK_NAME)  # type: ignore[untyped-decorator]
def process(task_id: str) -> None:
    task_log = log.bind(task_id=task_id)

    claimed = _claim(uuid.UUID(task_id))
    if claimed is None:
        task_log.info("task_skipped", reason="not pending")
        return
    task_log.info("task_started", attempt=claimed.attempts)

    counts = simulation.run(claimed.circuit, shots=SHOTS)

    if _complete(uuid.UUID(task_id), counts):
        task_log.info("task_completed")
    else:
        task_log.info("duplicate_result_discarded")
