import httpx
import pytest

from api_client import poll_until_final, submit_task

pytestmark = pytest.mark.integration

# compose.test.yaml shortens the worker's time limit to this.
TASK_TIMEOUT_SECONDS = 15


def test_invalid_circuit_reports_error_with_the_parse_error(api: httpx.Client) -> None:
    task_id = submit_task(api, "OPENQASM 3.0;\nqubit[2] q;\nnot_a_gate q[0];\n")

    final = poll_until_final(api, task_id)

    assert final["status"] == "error"
    message = final["message"]
    assert isinstance(message, str)
    assert message.startswith("Invalid QASM3: ")
    assert "not_a_gate" in message


def test_non_terminating_circuit_reports_a_timeout(api: httpx.Client) -> None:
    # The qubit is flipped to 1 and never changes, so the loop never ends.
    task_id = submit_task(
        api,
        'OPENQASM 3.0; include "stdgates.inc"; qubit[1] q; bit[1] c; '
        "x q[0]; c[0] = measure q[0]; while (c[0]) { c[0] = measure q[0]; }",
    )

    final = poll_until_final(api, task_id)

    assert final == {
        "status": "error",
        "message": f"Simulation exceeded the time limit of {TASK_TIMEOUT_SECONDS} seconds.",
    }
