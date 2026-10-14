import uuid
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa

from api_client import poll_until_final
from circuits import BELL_CIRCUIT
from common.data.tables import tasks

pytestmark = pytest.mark.integration

# The sweeper's default grace period; compose.yaml doesn't override it.
SWEEP_GRACE_SECONDS = 30


def test_task_stored_but_never_published_still_completes(
    api: httpx.Client, db_engine: sa.Engine
) -> None:
    # As if the API crashed between storing the task and publishing it, longer ago than the
    # grace period.
    task_id = uuid.uuid4()
    statement = tasks.insert().values(
        id=task_id,
        circuit=BELL_CIRCUIT,
        published=False,
        created_at=sa.func.now() - timedelta(seconds=2 * SWEEP_GRACE_SECONDS),
    )
    with db_engine.begin() as connection:
        connection.execute(statement)

    final = poll_until_final(api, str(task_id))

    assert final["status"] == "completed"
