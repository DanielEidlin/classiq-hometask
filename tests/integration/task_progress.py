"""Reads a task's attempt count and status straight from the stack's Postgres, for tests that
need to see what happened to a delivery rather than only the final outcome."""

import time
import uuid

import sqlalchemy as sa
from pydantic import BaseModel

from api_client import POLL_INTERVAL_SECONDS, TASK_DEADLINE_SECONDS
from common.data.tables import tasks
from common.domain.tasks import TaskStatus


class TaskProgress(BaseModel):
    attempts: int
    status: TaskStatus


def fetch_progress(engine: sa.Engine, task_id: uuid.UUID) -> TaskProgress:
    query = sa.select(tasks.c.attempts, tasks.c.status).where(tasks.c.id == task_id)
    with engine.connect() as connection:
        return TaskProgress.model_validate(connection.execute(query).one()._mapping)


def wait_until_started(engine: sa.Engine, task_id: uuid.UUID) -> None:
    deadline = time.monotonic() + TASK_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if fetch_progress(engine, task_id).attempts > 0:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"task {task_id} not started after {TASK_DEADLINE_SECONDS}s")
