import uuid

import httpx
import pytest
import sqlalchemy as sa

from api_client import poll_until_final, submit_task
from circuits import NON_TERMINATING_CIRCUIT
from common.domain.tasks import TaskStatus
from stack import TASK_TIMEOUT_SECONDS, ComposeStack
from task_progress import TaskProgress, fetch_progress, wait_until_started

pytestmark = [pytest.mark.integration, pytest.mark.resilience]


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
        wait_until_started(db_engine, parsed_id)
        # Every replica, so there's no guessing which one holds the task.
        compose_stack.kill("worker")
        interrupted = fetch_progress(db_engine, parsed_id)
    finally:
        compose_stack.start("worker")

    assert interrupted == TaskProgress(attempts=1, status=TaskStatus.PENDING)
    assert poll_until_final(api, task_id) == {
        "status": "error",
        "message": f"Simulation exceeded the time limit of {TASK_TIMEOUT_SECONDS} seconds.",
    }
    # The killed attempt and the one that timed out.
    assert fetch_progress(db_engine, parsed_id).attempts == 2
