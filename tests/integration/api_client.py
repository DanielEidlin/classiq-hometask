"""Helpers for driving the API over HTTP, shared by the integration tests."""

import time

import httpx

# The worker's default shot count; compose.yaml doesn't override it.
SHOTS = 1024

BELL_CIRCUIT = """
OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c = measure q;
"""

POLL_INTERVAL_SECONDS = 0.2
TASK_DEADLINE_SECONDS = 120
HEALTH_DEADLINE_SECONDS = 60


def submit_task(api: httpx.Client, circuit: str) -> str:
    """Submit the circuit, which must be accepted, and return its task ID."""
    response = api.post("/tasks", json={"qc": circuit})
    assert response.status_code == 202, response.text
    task_id: str = response.json()["task_id"]
    return task_id


def poll_until_final(
    api: httpx.Client, task_id: str, deadline_seconds: float = TASK_DEADLINE_SECONDS
) -> dict[str, object]:
    """Poll the task until it leaves `pending`, returning the final body."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        response = api.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        body: dict[str, object] = response.json()
        if body["status"] != "pending":
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"task {task_id} still pending after {deadline_seconds}s")


def wait_until_healthy(api: httpx.Client, deadline_seconds: float = HEALTH_DEADLINE_SECONDS) -> None:
    """Poll the API's health check until it reports Postgres and RabbitMQ reachable."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        if api.get("/health").status_code == 200:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"API still unhealthy after {deadline_seconds}s")
