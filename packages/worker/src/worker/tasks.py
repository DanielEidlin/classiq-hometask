"""The task handler: claims a pending task, simulates its circuit and records the counts."""

import functools
import os

import sqlalchemy as sa
import structlog

from common.message import PROCESS_TASK_NAME
from common.tasks import TaskStatus, tasks
from worker import simulation
from worker.app import app

log = structlog.get_logger()


@functools.cache
def _engine() -> sa.Engine:
    # Created lazily so each pool process opens its own connections after forking.
    return sa.create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


def _shots() -> int:
    return int(os.environ.get("SHOTS", "1024"))


@app.task(name=PROCESS_TASK_NAME)  # type: ignore[untyped-decorator]
def process(task_id: str) -> None:
    task_log = log.bind(task_id=task_id)
    still_pending = sa.and_(tasks.c.id == task_id, tasks.c.status == TaskStatus.PENDING)

    with _engine().begin() as conn:
        claimed = conn.execute(
            tasks.update()
            .where(still_pending)
            .values(attempts=tasks.c.attempts + 1, started_at=sa.func.now())
            .returning(tasks.c.qc, tasks.c.attempts)
        ).one_or_none()
    if claimed is None:
        task_log.info("task_skipped", reason="not pending")
        return
    task_log.info("task_started", attempt=claimed.attempts)

    counts = simulation.run(claimed.qc, shots=_shots())

    with _engine().begin() as conn:
        recorded = conn.execute(
            tasks.update()
            .where(still_pending)
            .values(status=TaskStatus.COMPLETED, result=counts, finished_at=sa.func.now())
        )
    if recorded.rowcount == 0:
        task_log.info("duplicate_result_discarded")
    else:
        task_log.info("task_completed")
