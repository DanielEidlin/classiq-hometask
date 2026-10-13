import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

BELL_CIRCUIT = """
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
"""


def poll_until_final(api: httpx.Client, task_id: str, deadline_seconds: float = 120) -> list[dict[str, object]]:
    """Poll the task until it leaves `pending`, returning every body observed."""
    observed: list[dict[str, object]] = []
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        response = api.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        body = response.json()
        observed.append(body)
        if body["status"] != "pending":
            return observed
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} still pending after {deadline_seconds}s")


def test_bell_circuit_is_pending_then_completed_with_counts(api: httpx.Client) -> None:
    response = api.post("/tasks", json={"qc": BELL_CIRCUIT})

    assert response.status_code == 202
    body = response.json()
    assert body["message"] == "Task submitted successfully."
    task_id = body["task_id"]
    assert uuid.UUID(task_id).version == 4

    observed = poll_until_final(api, task_id)

    assert observed[0] == {"status": "pending", "message": "Task is still in progress."}
    final = observed[-1]
    assert final["status"] == "completed"
    counts = final["result"]
    assert isinstance(counts, dict)
    assert set(counts) <= {"00", "11"}
    assert sum(counts.values()) == 1024


def test_unknown_task_id_returns_404(api: httpx.Client) -> None:
    response = api.get(f"/tasks/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"status": "error", "message": "Task not found."}


def test_health_is_ok_when_dependencies_are_reachable(api: httpx.Client) -> None:
    response = api.get("/health")

    assert response.status_code == 200
