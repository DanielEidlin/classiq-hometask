"""Binds the task handler to the task queue."""

import functools
import os
import uuid

from common.task_queue import PROCESS_TASK_NAME
from worker.app import app
from worker.handler import TaskHandler


# Built on first use: the engine's connection pool must not cross Celery's fork into its pool processes.
@functools.cache
def _handler() -> TaskHandler:
    return TaskHandler.connect(os.environ["DATABASE_URL"])


@app.task(name=PROCESS_TASK_NAME)  # type: ignore[untyped-decorator]
def process(task_id: str) -> None:
    _handler().handle(uuid.UUID(task_id))
