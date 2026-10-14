import httpx
import pytest

from api_client import poll_until_final, submit_task
from circuits import NON_TERMINATING_CIRCUIT
from stack import TASK_TIMEOUT_SECONDS

pytestmark = pytest.mark.integration


def test_invalid_circuit_reports_error_with_the_parse_error(api: httpx.Client) -> None:
    task_id = submit_task(api, "OPENQASM 3.0;\nqubit[2] q;\nnot_a_gate q[0];\n")

    final = poll_until_final(api, task_id)

    assert final["status"] == "error"
    message = final["message"]
    assert isinstance(message, str)
    assert message.startswith("Invalid QASM3: ")
    assert "not_a_gate" in message


def test_non_terminating_circuit_reports_a_timeout(api: httpx.Client) -> None:
    task_id = submit_task(api, NON_TERMINATING_CIRCUIT)

    final = poll_until_final(api, task_id)

    assert final == {
        "status": "error",
        "message": f"Simulation exceeded the time limit of {TASK_TIMEOUT_SECONDS} seconds.",
    }
