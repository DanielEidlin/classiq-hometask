"""The task queue's entry point: hands a delivered task to the handler."""

import uuid

from celery.exceptions import Reject

from worker.business.handler import TaskHandler
from worker.domain.abstractions import DatabaseUnavailable


def process_task(handler: TaskHandler, task_id: str) -> None:
    try:
        handler.handle(uuid.UUID(task_id))
    except DatabaseUnavailable:
        # Unacknowledged and requeued, so the task runs again once the database is back.
        raise Reject("the database is unavailable", requeue=True) from None
