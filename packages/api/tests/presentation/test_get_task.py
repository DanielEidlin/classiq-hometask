import uuid

import pytest
from fastapi.testclient import TestClient

from api.domain.outcomes import CompletedOutcome, FailedOutcome, PendingOutcome, TaskOutcome
from common.domain.tasks import TaskStatus
from fakes.api_task_store import FakeTaskStore

COUNTS = {"00": 512, "11": 512}
FAILURE_MESSAGE = "Simulation exceeded the time limit of 60 seconds."


@pytest.mark.parametrize(
    ("outcome", "body"),
    [
        pytest.param(
            PendingOutcome(status=TaskStatus.PENDING),
            {"status": "pending", "message": "Task is still in progress."},
            id="pending",
        ),
        pytest.param(
            CompletedOutcome(status=TaskStatus.COMPLETED, result=COUNTS),
            {"status": "completed", "result": COUNTS},
            id="completed",
        ),
        pytest.param(
            FailedOutcome(status=TaskStatus.FAILED, error_message=FAILURE_MESSAGE),
            {"status": "error", "message": FAILURE_MESSAGE},
            id="failed",
        ),
    ],
)
def test_each_stored_outcome_is_returned_in_its_response_shape(
    client: TestClient, task_store: FakeTaskStore, outcome: TaskOutcome, body: dict[str, object]
) -> None:
    task_id = uuid.uuid4()
    task_store.outcomes[task_id] = outcome

    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json() == body


@pytest.mark.parametrize("task_id", [str(uuid.uuid4()), "not-a-uuid"], ids=["unknown", "malformed"])
def test_a_task_that_does_not_exist_returns_404(client: TestClient, task_id: str) -> None:
    response = client.get(f"/tasks/{task_id}")

    assert response.status_code == 404
    assert response.json() == {"status": "error", "message": "Task not found."}


def test_health_is_503_when_postgres_is_unreachable(
    client: TestClient, task_store: FakeTaskStore
) -> None:
    task_store.reachable = False

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "message": "Postgres is unreachable."}
