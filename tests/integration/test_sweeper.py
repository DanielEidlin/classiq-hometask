import uuid

import httpx
import pytest

from api_client import poll_until_final
from circuits import BELL_CIRCUIT
from stack import ComposeStack

pytestmark = pytest.mark.integration

# The sweeper's default grace period; compose.yaml doesn't override it.
SWEEP_GRACE_SECONDS = 30


def test_task_stored_but_never_published_still_completes(
    api: httpx.Client, compose_stack: ComposeStack
) -> None:
    # As if the API crashed between storing the task and publishing it, longer ago than the
    # grace period.
    task_id = uuid.uuid4()
    compose_stack.execute_sql(
        "INSERT INTO tasks (id, circuit, published, created_at) "
        f"VALUES ('{task_id}', $circuit${BELL_CIRCUIT}$circuit$, false, "
        f"now() - interval '{2 * SWEEP_GRACE_SECONDS} seconds')"
    )

    final = poll_until_final(api, str(task_id))

    assert final["status"] == "completed"
