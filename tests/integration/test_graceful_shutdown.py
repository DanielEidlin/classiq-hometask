import json
import uuid

import httpx
import pytest
import sqlalchemy as sa

from api_client import poll_until_final, submit_task
from circuits import NON_TERMINATING_CIRCUIT
from common.tasks import TaskStatus
from stack import TASK_TIMEOUT_SECONDS, ComposeStack
from task_progress import TaskProgress, fetch_progress, wait_until_started

pytestmark = [pytest.mark.integration, pytest.mark.resilience]

# structlog's level names (`common.log`) and the prefix of the plain-text lines Uvicorn writes
# through the standard library's default formatter.
ERROR_LEVELS = {"error", "critical"}
PLAIN_TEXT_ERROR_PREFIXES = ("ERROR", "CRITICAL", "Traceback")


def _is_error(line: str) -> bool:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return line.startswith(PLAIN_TEXT_ERROR_PREFIXES)
    return isinstance(entry, dict) and entry.get("level") in ERROR_LEVELS


def test_task_running_when_the_workers_are_stopped_finishes_without_being_redelivered(
    api: httpx.Client, compose_stack: ComposeStack, db_engine: sa.Engine
) -> None:
    """A circuit that never finishes, so the stop always lands mid-simulation and the attempt
    ends at the worker's time limit, which the test stack sets well inside the stop grace
    period. The outcome is a timeout rather than counts; that it is recorded by the first
    attempt, before the worker exits, is what shows the shutdown waited for it."""
    task_id = submit_task(api, NON_TERMINATING_CIRCUIT)
    parsed_id = uuid.UUID(task_id)
    try:
        wait_until_started(db_engine, parsed_id)
        # Every replica, so there's no guessing which one holds the task.
        compose_stack.stop("worker")
        after_stop = fetch_progress(db_engine, parsed_id)
        exit_codes = compose_stack.exit_codes("worker")
    finally:
        compose_stack.start("worker")

    assert after_stop == TaskProgress(attempts=1, status=TaskStatus.FAILED)
    assert set(exit_codes) == {0}
    assert poll_until_final(api, task_id) == {
        "status": "error",
        "message": f"Simulation exceeded the time limit of {TASK_TIMEOUT_SECONDS} seconds.",
    }


@pytest.mark.parametrize("service", ["api", "sweeper"])
def test_service_exits_cleanly_on_sigterm(compose_stack: ComposeStack, service: str) -> None:
    lines_before_stop = len(compose_stack.log_lines(service))
    try:
        compose_stack.stop(service)
        exit_codes = compose_stack.exit_codes(service)
        shutdown_lines = compose_stack.log_lines(service)[lines_before_stop:]
    finally:
        compose_stack.start_and_wait(service)

    assert set(exit_codes) == {0}
    assert [line for line in shutdown_lines if _is_error(line)] == []
