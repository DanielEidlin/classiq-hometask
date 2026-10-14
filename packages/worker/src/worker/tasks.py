"""Builds the task handler from its production parts and binds it to the task queue."""

import functools
import os

from common.data.task_queue import PROCESS_TASK_NAME
from worker.app import app
from worker.business.handler import TaskHandler
from worker.business.runner import SimulationRunner
from worker.data.database import create_database_engine, retry_while_unavailable
from worker.data.store import PostgresTaskStore
from worker.presentation.tasks import process_task
from worker.settings import (
    DB_OUTAGE_GIVE_UP_SECONDS,
    DB_RETRY_FIRST_DELAY_SECONDS,
    DB_RETRY_MAX_DELAY_SECONDS,
    DB_TIMEOUT_SECONDS,
    MAX_ATTEMPTS,
    SIMULATION_LIMITS,
)


# Built on first use: the engine's connection pool must not cross Celery's fork into its pool processes.
@functools.cache
def _handler() -> TaskHandler:
    store = PostgresTaskStore(
        create_database_engine(os.environ["DATABASE_URL"], DB_TIMEOUT_SECONDS),
        retry_while_unavailable(
            DB_RETRY_FIRST_DELAY_SECONDS, DB_RETRY_MAX_DELAY_SECONDS, DB_OUTAGE_GIVE_UP_SECONDS
        ),
    )
    return TaskHandler(store, SimulationRunner(SIMULATION_LIMITS), MAX_ATTEMPTS)


@app.task(name=PROCESS_TASK_NAME)  # type: ignore[untyped-decorator]
def process(task_id: str) -> None:
    process_task(_handler(), task_id)
