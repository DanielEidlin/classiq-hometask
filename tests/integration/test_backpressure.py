import httpx
import pytest

from stack import MAX_QUEUED_TASKS, ComposeStack
from test_core_flow import BELL_CIRCUIT, poll_until_final

pytestmark = pytest.mark.integration

SUBMISSIONS_OVER_THE_CAP = 3


def test_submissions_beyond_the_queue_cap_get_503_and_accepted_tasks_complete(
    api: httpx.Client, compose_stack: ComposeStack
) -> None:
    # With no worker consuming, every accepted task stays in the queue.
    compose_stack.stop("worker")
    try:
        responses = [
            api.post("/tasks", json={"qc": BELL_CIRCUIT})
            for _ in range(MAX_QUEUED_TASKS + SUBMISSIONS_OVER_THE_CAP)
        ]
    finally:
        compose_stack.start("worker")

    accepted = [response for response in responses if response.status_code == 202]
    rejected = [response for response in responses if response.status_code != 202]
    # At most the cap: tasks left in the queue by earlier tests would take some of the room.
    assert 0 < len(accepted) <= MAX_QUEUED_TASKS
    assert len(rejected) >= SUBMISSIONS_OVER_THE_CAP
    for response in rejected:
        assert response.status_code == 503
        assert int(response.headers["retry-after"]) > 0
        assert response.json()["status"] == "error"

    for response in accepted:
        assert poll_until_final(api, response.json()["task_id"])["status"] == "completed"
