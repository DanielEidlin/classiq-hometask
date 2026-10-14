"""The worker's task handler, with its task store and circuit runner replaced.

What the store does with the database, including keeping only the first outcome of a task
run twice, is tested on the real store (`test_store.py`).
"""

import uuid
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

from common.domain.tasks import ErrorCode, TaskStatus
from tests.support.fakes.worker_task_store import FakeTaskStore
from worker.business.handler import WORKER_LOST_MESSAGE, TaskHandler
from worker.domain.abstractions import DatabaseUnavailable, StartedAttempt
from worker.domain.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

MAX_ATTEMPTS = 3
# Never simulated: every test replaces the circuit runner.
CIRCUIT = "OPENQASM 3.0;"
COUNTS = {"00": 1024}
FIRST_ATTEMPT = StartedAttempt(circuit=CIRCUIT, number=1)


def _runner(outcome: SimulationOutcome = SimulationCompleted(counts=COUNTS)) -> Mock:
    circuit_runner = Mock(spec_set=["run"])
    circuit_runner.run.return_value = outcome
    return circuit_runner


def test_the_circuit_is_simulated_and_its_outcome_recorded() -> None:
    store = FakeTaskStore(started=FIRST_ATTEMPT)
    circuit_runner = _runner()

    with capture_logs() as logs:
        TaskHandler(store, circuit_runner, MAX_ATTEMPTS).handle(uuid.uuid4())

    circuit_runner.run.assert_called_once_with(CIRCUIT)
    assert store.recorded == [SimulationCompleted(counts=COUNTS)]
    assert "task_completed" in [entry["event"] for entry in logs]


def test_task_past_the_attempt_cap_fails_with_worker_lost_without_simulating() -> None:
    store = FakeTaskStore(started=StartedAttempt(circuit=CIRCUIT, number=MAX_ATTEMPTS + 1))
    circuit_runner = _runner()

    TaskHandler(store, circuit_runner, MAX_ATTEMPTS).handle(uuid.uuid4())

    circuit_runner.run.assert_not_called()
    assert store.recorded == [
        SimulationFailed(
            code=ErrorCode.WORKER_LOST,
            message=WORKER_LOST_MESSAGE.format(max_attempts=MAX_ATTEMPTS),
        )
    ]


def test_task_at_the_attempt_cap_is_still_simulated() -> None:
    store = FakeTaskStore(started=StartedAttempt(circuit=CIRCUIT, number=MAX_ATTEMPTS))
    circuit_runner = _runner()

    TaskHandler(store, circuit_runner, MAX_ATTEMPTS).handle(uuid.uuid4())

    circuit_runner.run.assert_called_once_with(CIRCUIT)


def test_an_outcome_another_attempt_beat_is_logged_as_discarded() -> None:
    task_id = uuid.uuid4()
    store = FakeTaskStore(started=FIRST_ATTEMPT, outcome_is_first=False)

    with capture_logs() as logs:
        TaskHandler(store, _runner(), MAX_ATTEMPTS).handle(task_id)

    assert {"event": "duplicate_result_discarded", "task_id": str(task_id), "log_level": "info"} in logs
    assert "task_completed" not in [entry["event"] for entry in logs]


def test_final_task_is_skipped() -> None:
    task_id = uuid.uuid4()
    store = FakeTaskStore(started=None, status=TaskStatus.COMPLETED)
    circuit_runner = _runner()

    with capture_logs() as logs:
        TaskHandler(store, circuit_runner, MAX_ATTEMPTS).handle(task_id)

    circuit_runner.run.assert_not_called()
    assert store.recorded == []
    assert {
        "event": "task_skipped",
        "task_id": str(task_id),
        "reason": "already final",
        "status": TaskStatus.COMPLETED,
        "log_level": "info",
    } in logs


def test_missing_task_is_skipped() -> None:
    task_id = uuid.uuid4()
    store = FakeTaskStore(started=None, status=None)
    circuit_runner = _runner()

    with capture_logs() as logs:
        TaskHandler(store, circuit_runner, MAX_ATTEMPTS).handle(task_id)

    circuit_runner.run.assert_not_called()
    assert {
        "event": "task_skipped",
        "task_id": str(task_id),
        "reason": "not found",
        "log_level": "info",
    } in logs


def test_an_unavailable_database_stops_the_task_before_it_is_simulated() -> None:
    circuit_runner = _runner()

    with pytest.raises(DatabaseUnavailable):
        TaskHandler(FakeTaskStore(unavailable=True), circuit_runner, MAX_ATTEMPTS).handle(uuid.uuid4())

    circuit_runner.run.assert_not_called()
