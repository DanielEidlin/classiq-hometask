import uuid

import httpx
import pytest

from tests.integration.api_client import poll_until_final
from tests.integration.circuits import BELL_CIRCUIT
from tests.integration.stack import ComposeStack

pytestmark = pytest.mark.integration

# The worker's default shot count; compose.yaml doesn't override it.
SHOTS = 1024
# The API's default circuit size limit, which compose.yaml passes through unless MAX_CIRCUIT_BYTES is set.
MAX_CIRCUIT_BYTES = 65536


def test_bell_circuit_is_pending_then_completed_with_counts(
    api: httpx.Client, compose_stack: ComposeStack
) -> None:
    # With no worker running, the task cannot leave `pending` before it is polled.
    compose_stack.stop("worker")
    try:
        response = api.post("/tasks", json={"qc": BELL_CIRCUIT})

        assert response.status_code == 202
        body = response.json()
        assert body["message"] == "Task submitted successfully."
        task_id = body["task_id"]
        assert uuid.UUID(task_id).version == 4

        pending = api.get(f"/tasks/{task_id}")
        assert pending.status_code == 200
        assert pending.json() == {"status": "pending", "message": "Task is still in progress."}
    finally:
        compose_stack.start("worker")

    final = poll_until_final(api, task_id)
    assert final["status"] == "completed"
    counts = final["result"]
    assert isinstance(counts, dict)
    # Subset, not equality: sampling can miss one of the two outcomes.
    assert set(counts) <= {"00", "11"}
    assert sum(counts.values()) == SHOTS


def test_unknown_task_id_returns_404(api: httpx.Client) -> None:
    response = api.get(f"/tasks/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"status": "error", "message": "Task not found."}


def test_malformed_body_returns_400(api: httpx.Client) -> None:
    response = api.post("/tasks", json={"circuit": BELL_CIRCUIT})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "body.qc: Field required"}


def test_oversized_circuit_returns_413(api: httpx.Client) -> None:
    oversized_circuit = BELL_CIRCUIT + " " * MAX_CIRCUIT_BYTES

    response = api.post("/tasks", json={"qc": oversized_circuit})

    assert response.status_code == 413
    assert response.json() == {
        "status": "error",
        "message": f"Circuit is larger than the limit of {MAX_CIRCUIT_BYTES} bytes.",
    }


def test_health_is_ok_when_dependencies_are_reachable(api: httpx.Client) -> None:
    response = api.get("/health")

    assert response.status_code == 200
