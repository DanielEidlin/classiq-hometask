import httpx
import pytest

from stack import ComposeStack
from api_client import poll_until_final, submit_task, wait_until_healthy
from circuits import BELL_CIRCUIT

pytestmark = [pytest.mark.integration, pytest.mark.resilience]

TASKS_QUEUED_BEFORE_RESTART = 5


def test_queued_tasks_survive_a_rabbitmq_restart_and_the_api_reconnects(
    api: httpx.Client, compose_stack: ComposeStack
) -> None:
    """Completion is the evidence that the queued messages survived.

    This relies on nothing republishing published tasks: the sweeper republishes only
    unpublished ones, so a task whose message the restart lost would stay pending and fail
    here. A future mechanism that republishes stale tasks would hide such a loss.
    """
    # With the workers stopped, every task is still a message in the queue during the restart.
    compose_stack.stop("worker")
    try:
        queued_task_ids = [
            submit_task(api, BELL_CIRCUIT) for _ in range(TASKS_QUEUED_BEFORE_RESTART)
        ]
        compose_stack.restart("rabbitmq")
        wait_until_healthy(api)
        task_id_after_restart = submit_task(api, BELL_CIRCUIT)
    finally:
        # A no-op unless the restart failed partway.
        compose_stack.start("rabbitmq")
        compose_stack.start("worker")

    for task_id in [*queued_task_ids, task_id_after_restart]:
        assert poll_until_final(api, task_id)["status"] == "completed"


def test_queued_tasks_survive_the_rabbitmq_container_being_replaced(
    api: httpx.Client, compose_stack: ComposeStack
) -> None:
    """A new container must find the old one's messages on the data volume, which holds
    them under the node's name, derived from the hostname."""
    compose_stack.stop("worker")
    try:
        queued_task_ids = [
            submit_task(api, BELL_CIRCUIT) for _ in range(TASKS_QUEUED_BEFORE_RESTART)
        ]
        compose_stack.recreate("rabbitmq")
        wait_until_healthy(api)
    finally:
        # A no-op unless the recreate failed partway.
        compose_stack.start("rabbitmq")
        compose_stack.start("worker")

    for task_id in queued_task_ids:
        assert poll_until_final(api, task_id)["status"] == "completed"
