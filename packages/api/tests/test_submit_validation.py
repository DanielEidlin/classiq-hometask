from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.tasks import MAX_CIRCUIT_BYTES


def test_missing_qc_returns_400(client: TestClient) -> None:
    response = client.post("/tasks", json={})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "Field 'qc' is required."}


@pytest.mark.parametrize("qc", [5, None, ["OPENQASM 3.0;"]])
def test_non_string_qc_returns_400(client: TestClient, qc: object) -> None:
    response = client.post("/tasks", json={"qc": qc})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "Field 'qc' must be a string."}


def test_empty_qc_returns_400(client: TestClient) -> None:
    response = client.post("/tasks", json={"qc": ""})

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "Field 'qc' must not be empty."}


def test_body_that_is_not_json_returns_400(client: TestClient) -> None:
    response = client.post(
        "/tasks", content=b'{"qc": ', headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert response.json() == {"status": "error", "message": "Request body is not valid JSON."}


@pytest.mark.parametrize("body", [["OPENQASM 3.0;"], "OPENQASM 3.0;", None])
def test_body_that_is_not_an_object_returns_400(client: TestClient, body: object) -> None:
    response = client.post("/tasks", json=body)

    assert response.status_code == 400
    assert response.json() == {
        "status": "error",
        "message": "Request body must be a JSON object.",
    }


def test_circuit_over_the_size_limit_returns_413_and_is_not_stored(
    client: TestClient, db_connection: AsyncMock, publisher: AsyncMock
) -> None:
    oversized_circuit = "x" * (MAX_CIRCUIT_BYTES + 1)

    response = client.post("/tasks", json={"qc": oversized_circuit})

    assert response.status_code == 413
    assert response.json() == {
        "status": "error",
        "message": f"Circuit is larger than the limit of {MAX_CIRCUIT_BYTES} bytes.",
    }
    db_connection.execute.assert_not_awaited()
    publisher.publish.assert_not_awaited()


def test_circuit_size_is_measured_in_utf8_bytes(client: TestClient) -> None:
    # "é" is 2 bytes in UTF-8, so this is under the limit in characters but over it in bytes.
    oversized_circuit = "é" * (MAX_CIRCUIT_BYTES // 2 + 1)

    response = client.post("/tasks", json={"qc": oversized_circuit})

    assert response.status_code == 413
