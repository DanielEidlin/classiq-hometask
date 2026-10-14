"""Builds the task handler from its production parts and binds it to the task queue."""

import functools
import os
import uuid

from celery.exceptions import Reject

from common.task_queue import PROCESS_TASK_NAME
from worker.app import app
from worker.abstractions import DatabaseUnavailable
from worker.handler import TaskHandler
from worker.runner import SimulationRunner
from worker.settings import DB_RETRY_POLICY, DB_TIMEOUT_SECONDS, MAX_ATTEMPTS, SIMULATION_LIMITS
from worker.store import PostgresTaskStore, create_database_engine


# Built on first use: the engine's connection pool must not cross Celery's fork into its pool processes.
@functools.cache
def _handler() -> TaskHandler:
    store = PostgresTaskStore(
        create_database_engine(os.environ["DATABASE_URL"], DB_TIMEOUT_SECONDS), DB_RETRY_POLICY
    )
    return TaskHandler(store, SimulationRunner(SIMULATION_LIMITS), MAX_ATTEMPTS)


@app.task(name=PROCESS_TASK_NAME)  # type: ignore[untyped-decorator]
def process(task_id: str) -> None:
    try:
        _handler().handle(uuid.UUID(task_id))
    except DatabaseUnavailable:
        # Unacknowledged and requeued, so the task runs again once the database is back.
        raise Reject("the database is unavailable", requeue=True) from None
