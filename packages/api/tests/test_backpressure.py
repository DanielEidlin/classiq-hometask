from unittest.mock import AsyncMock

import sqlalchemy as sa
from fastapi.testclient import TestClient

from api.tasks import RETRY_AFTER_SECONDS
from common.publisher import PublishFailed
from common.tasks import tasks

CIRCUIT = "OPENQASM 3.0;"


def test_failed_publish_returns_503_with_retry_after(
    client: TestClient, publisher: AsyncMock
) -> None:
    publisher.publish.side_effect = PublishFailed("rejected")

    response = client.post("/tasks", json={"qc": CIRCUIT})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
    assert response.json() == {
        "status": "error",
        "message": "The service is busy. Submit the task again later.",
    }


def test_failed_publish_deletes_the_inserted_task(
    client: TestClient, db_connection: AsyncMock, publisher: AsyncMock
) -> None:
    publisher.publish.side_effect = PublishFailed("rejected")

    client.post("/tasks", json={"qc": CIRCUIT})

    statements = [call.args[0] for call in db_connection.execute.await_args_list]
    inserted_id = statements[0].compile().params["id"]
    deletes = [statement for statement in statements if isinstance(statement, sa.Delete)]
    assert len(deletes) == 1
    assert deletes[0].table is tasks
    assert deletes[0].compile().params == {"id_1": inserted_id}


def test_successful_publish_does_not_delete_the_task(
    client: TestClient, db_connection: AsyncMock
) -> None:
    response = client.post("/tasks", json={"qc": CIRCUIT})

    assert response.status_code == 202
    statements = [call.args[0] for call in db_connection.execute.await_args_list]
    assert not any(isinstance(statement, sa.Delete) for statement in statements)
