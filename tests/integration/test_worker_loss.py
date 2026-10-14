import time
import uuid

import httpx
import pytest
import sqlalchemy as sa
from pydantic import BaseModel

from api_client import POLL_INTERVAL_SECONDS, TASK_DEADLINE_SECONDS, poll_until_final, submit_task
from circuits import NON_TERMINATING_CIRCUIT
from common.tasks import TaskStatus, tasks
from stack import TASK_TIMEOUT_SECONDS, ComposeStack

pytestmark = [pytest.mark.integration, pytest.mark.resilience]


class TaskProgress(BaseModel):
    attempts: int
    status: TaskStatus


def _fetch_progress(engine: sa.Engine, task_id: uuid.UUID) -> TaskProgress:
    query = sa.select(tasks.c.attempts, tasks.c.status).where(tasks.c.id == task_id)
    with engine.connect() as connection:
        return TaskProgress.model_validate(connection.execute(query).one()._mapping)


def _wait_until_started(engine: sa.Engine, task_id: uuid.UUID) -> None:
    deadline = time.monotonic() + TASK_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if _fetch_progress(engine, task_id).attempts > 0:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"task {task_id} not started after {TASK_DEADLINE_SECONDS}s")


def test_task_whose_workers_are_killed_mid_simulation_is_run_again_after_they_restart(
    api: httpx.Client, compose_stack: ComposeStack, db_engine: sa.Engine
) -> None:
    """A circuit that never finishes, so the kill always lands mid-simulation: the only window
    is the worker's time limit, which the test stack sets. The cost is that the attempt run
    after the restart ends in a timeout rather than counts; reaching a final state is what
    shows the task wasn't lost."""
    task_id = submit_task(api, NON_TERMINATING_CIRCUIT)
    parsed_id = uuid.UUID(task_id)
    try:
        _wait_until_started(db_engine, parsed_id)
        # Every replica, so there's no guessing which one holds the task.
        compose_stack.kill("worker")
        interrupted = _fetch_progress(db_engine, parsed_id)
    finally:
        compose_stack.start("worker")

    assert interrupted == TaskProgress(attempts=1, status=TaskStatus.PENDING)
    assert poll_until_final(api, task_id) == {
        "status": "error",
        "message": f"Simulation exceeded the time limit of {TASK_TIMEOUT_SECONDS} seconds.",
    }
    # The killed attempt and the one that timed out.
    assert _fetch_progress(db_engine, parsed_id).attempts == 2
