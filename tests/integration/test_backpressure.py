import httpx
import pytest

from common.settings import MAX_QUEUED_TASKS
from tests.integration.api_client import poll_until_final
from tests.integration.circuits import BELL_CIRCUIT
from tests.integration.stack import ComposeStack

pytestmark = pytest.mark.integration

TEST_QUEUE_CAP = 5
SUBMISSIONS_OVER_THE_CAP = 3


def test_task_queue_is_capped_and_rejects_publishes_when_full(compose_stack: ComposeStack) -> None:
    # The test process reads MAX_QUEUED_TASKS from the same environment variable and default
    # that compose.yaml passes to the services.
    arguments = compose_stack.task_queue_arguments()

    assert arguments["x-max-length"] == MAX_QUEUED_TASKS
    assert arguments["x-overflow"] == "reject-publish"


def test_submissions_beyond_the_queue_cap_get_503_and_accepted_tasks_complete(
    api: httpx.Client, compose_stack: ComposeStack
) -> None:
    # With no worker consuming, every accepted task stays in the queue.
    try:
        compose_stack.stop("worker")
        compose_stack.purge_task_queue()
        # A policy for this test only, so filling the queue is quick and other tests keep the
        # configured cap.
        compose_stack.cap_task_queue(TEST_QUEUE_CAP)
        responses = [
            api.post("/tasks", json={"qc": BELL_CIRCUIT})
            for _ in range(TEST_QUEUE_CAP + SUBMISSIONS_OVER_THE_CAP)
        ]
    finally:
        compose_stack.remove_task_queue_cap()
        compose_stack.start("worker")

    accepted = [response for response in responses if response.status_code == 202]
    rejected = [response for response in responses if response.status_code != 202]
    assert len(accepted) == TEST_QUEUE_CAP
    assert len(rejected) == SUBMISSIONS_OVER_THE_CAP
    for response in rejected:
        assert response.status_code == 503
        assert int(response.headers["retry-after"]) > 0
        assert response.json()["status"] == "error"

    for response in accepted:
        assert poll_until_final(api, response.json()["task_id"])["status"] == "completed"
