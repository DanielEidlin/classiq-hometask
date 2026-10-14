from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.presentation.tasks import CIRCUIT_TOO_LARGE_MESSAGE
from api.settings import MAX_CIRCUIT_BYTES
from fakes.api_task_store import FakeTaskStore


def test_missing_qc_returns_400(client: TestClient) -> None:
    response = client.post("/tasks", json={})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "body.qc: Field required"}


@pytest.mark.parametrize("qc", [5, None, ["OPENQASM 3.0;"]])
def test_non_string_qc_returns_400(client: TestClient, qc: object) -> None:
    response = client.post("/tasks", json={"qc": qc})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "body.qc: Input should be a valid string"}


def test_empty_qc_returns_400(client: TestClient) -> None:
    response = client.post("/tasks", json={"qc": ""})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "body.qc: String should have at least 1 character"}


def test_body_that_is_not_json_returns_400(client: TestClient) -> None:
    response = client.post(
        "/tasks", content=b'{"qc": ', headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "body.7: JSON decode error"}


@pytest.mark.parametrize("body", [["OPENQASM 3.0;"], "OPENQASM 3.0;"])
def test_body_that_is_not_an_object_returns_400(client: TestClient, body: object) -> None:
    response = client.post("/tasks", json=body)

    assert response.status_code == 400
    assert response.json() == {
        "status": "error",
        "message": "body: Input should be a valid dictionary or object to extract fields from",
    }


def test_missing_body_returns_400(client: TestClient) -> None:
    response = client.post("/tasks")

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "body: Field required"}


def test_circuit_over_the_size_limit_returns_413_and_is_not_stored(
    client: TestClient, task_store: FakeTaskStore, publisher: AsyncMock
) -> None:
    oversized_circuit = "x" * (MAX_CIRCUIT_BYTES + 1)

    response = client.post("/tasks", json={"qc": oversized_circuit})

    assert response.status_code == 413
    assert response.json() == {"status": "error", "message": CIRCUIT_TOO_LARGE_MESSAGE}
    assert task_store.inserted == []
    publisher.publish.assert_not_awaited()


def test_circuit_size_is_measured_in_utf8_bytes(client: TestClient) -> None:
    two_byte_character = b"\xc3\xa9".decode("utf-8")
    oversized_circuit = two_byte_character * (MAX_CIRCUIT_BYTES // 2 + 1)

    response = client.post("/tasks", json={"qc": oversized_circuit})

    assert response.status_code == 413
