"""The worker's task handler against a real Postgres, with the circuit runner replaced.

Duplicate handling is tested here rather than end to end: arranging an overlapping
redelivery through the queue would depend on timing, while calling the handler is
deterministic.
"""

import uuid
from collections.abc import Callable, Iterator
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
import tenacity
from structlog.testing import capture_logs

from common.tasks import ErrorCode, TaskStatus, tasks
from fakes.circuit_runners import RunnerThatTakesTheDatabaseDown, RunnerThatTriggersARedelivery
from fakes.database_outage import DatabaseOutage
from worker.abstractions import CircuitRunner
from worker.database import retry_while_unavailable
from worker.handler import WORKER_LOST_MESSAGE, DatabaseUnavailable, TaskHandler
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

MAX_ATTEMPTS = 3
# Never simulated: every test replaces the circuit runner.
CIRCUIT = "OPENQASM 3.0;"
FIRST_COUNTS = {"00": 1024}
SECOND_COUNTS = {"11": 1024}
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
# Unused: the tests give up after FAILED_CALLS_BEFORE_GIVING_UP instead of after a delay.
GIVE_UP_SECONDS = 240.0
FAILED_CALLS_BEFORE_GIVING_UP = 4
# The backoff between those four failed calls.
DELAYS_UNTIL_GIVING_UP = [1.0, 2.0, 4.0]
FAILED_CALLS_BEFORE_RECOVERY = 2


def _insert_task(
    engine: sa.Engine,
    status: TaskStatus = TaskStatus.PENDING,
    attempts: int = 0,
    result: dict[str, int] | None = None,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    statement = tasks.insert().values(
        id=task_id,
        circuit=CIRCUIT,
        status=status,
        attempts=attempts,
        result=result,
    )
    with engine.begin() as connection:
        connection.execute(statement)
    return task_id


def _fetch_task(engine: sa.Engine, task_id: uuid.UUID) -> sa.RowMapping:
    with engine.connect() as connection:
        return connection.execute(sa.select(tasks).where(tasks.c.id == task_id)).mappings().one()


def _runner(outcome: SimulationOutcome) -> Mock:
    circuit_runner = Mock(spec_set=["run"])
    circuit_runner.run.return_value = outcome
    return circuit_runner


def _discard_sleep(seconds: float) -> None:
    return None


def _retrying(sleep: Callable[[float], None] = _discard_sleep) -> tenacity.Retrying:
    """The production retry and backoff, giving up after a number of failed calls rather
    than after a delay, so that sleeping can be skipped."""
    return retry_while_unavailable(FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS, GIVE_UP_SECONDS).copy(
        stop=tenacity.stop_after_attempt(FAILED_CALLS_BEFORE_GIVING_UP), sleep=sleep
    )


def _handler(
    engine: sa.Engine, circuit_runner: CircuitRunner, retrying: tenacity.Retrying | None = None
) -> TaskHandler:
    return TaskHandler(engine, circuit_runner, MAX_ATTEMPTS, retrying or _retrying())


@pytest.fixture
def outage_engine(db_engine: sa.Engine) -> Iterator[sa.Engine]:
    """An engine of its own on the test database, so an outage's listener doesn't outlive the test."""
    engine = sa.create_engine(db_engine.url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_running_a_task_twice_keeps_the_first_result(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine)
    redelivery = _handler(db_engine, _runner(SimulationCompleted(counts=FIRST_COUNTS)))
    handler = _handler(
        db_engine,
        RunnerThatTriggersARedelivery(redelivery, task_id, SimulationCompleted(counts=SECOND_COUNTS)),
    )

    with capture_logs() as logs:
        handler.handle(task_id)

    task = _fetch_task(db_engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS
    assert task["attempts"] == 2
    assert task["finished_at"] is not None
    assert {"event": "duplicate_result_discarded", "task_id": str(task_id), "log_level": "info"} in logs


@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param(SimulationCompleted(counts=FIRST_COUNTS), id="completed"),
        pytest.param(
            SimulationFailed(code=ErrorCode.INVALID_CIRCUIT, message="Invalid QASM3: ..."),
            id="failed",
        ),
    ],
)
def test_outcome_is_recorded_with_its_finish_time(
    db_engine: sa.Engine, outcome: SimulationOutcome
) -> None:
    task_id = _insert_task(db_engine)

    _handler(db_engine, _runner(outcome)).handle(task_id)

    task = _fetch_task(db_engine, task_id)
    assert task["status"] == outcome.status
    assert task["attempts"] == 1
    assert task["started_at"] is not None
    assert task["finished_at"] is not None


def test_task_at_the_attempt_cap_fails_with_worker_lost_without_simulating(
    db_engine: sa.Engine,
) -> None:
    task_id = _insert_task(db_engine, attempts=MAX_ATTEMPTS)
    circuit_runner = _runner(SimulationCompleted(counts=FIRST_COUNTS))

    _handler(db_engine, circuit_runner).handle(task_id)

    circuit_runner.run.assert_not_called()
    task = _fetch_task(db_engine, task_id)
    assert task["status"] == TaskStatus.FAILED
    assert task["error_code"] == ErrorCode.WORKER_LOST
    assert task["error_message"] == WORKER_LOST_MESSAGE.format(max_attempts=MAX_ATTEMPTS)
    assert task["finished_at"] is not None


def test_final_task_is_skipped(db_engine: sa.Engine) -> None:
    task_id = _insert_task(
        db_engine, status=TaskStatus.COMPLETED, result=FIRST_COUNTS, attempts=1
    )
    before = _fetch_task(db_engine, task_id)
    circuit_runner = _runner(SimulationCompleted(counts=SECOND_COUNTS))

    with capture_logs() as logs:
        _handler(db_engine, circuit_runner).handle(task_id)

    circuit_runner.run.assert_not_called()
    assert _fetch_task(db_engine, task_id) == before
    assert {
        "event": "task_skipped",
        "task_id": str(task_id),
        "reason": "already final",
        "status": TaskStatus.COMPLETED,
        "log_level": "info",
    } in logs


def test_missing_task_is_skipped(db_engine: sa.Engine) -> None:
    task_id = uuid.uuid4()
    circuit_runner = _runner(SimulationCompleted(counts=FIRST_COUNTS))

    with capture_logs() as logs:
        _handler(db_engine, circuit_runner).handle(task_id)

    circuit_runner.run.assert_not_called()
    assert {
        "event": "task_skipped",
        "task_id": str(task_id),
        "reason": "not found",
        "log_level": "info",
    } in logs


def test_outcome_is_given_up_on_for_requeue_while_the_database_stays_unreachable(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    sleeps: list[float] = []
    handler = _handler(outage_engine, RunnerThatTakesTheDatabaseDown(outage, SimulationCompleted(counts=FIRST_COUNTS)), _retrying(sleeps.append))

    with capture_logs() as logs, pytest.raises(DatabaseUnavailable):
        handler.handle(task_id)

    assert sleeps == DELAYS_UNTIL_GIVING_UP
    failed_calls = [entry for entry in logs if entry["event"] == "database_call_failed"]
    assert len(failed_calls) == FAILED_CALLS_BEFORE_GIVING_UP
    assert all(
        entry["task_id"] == str(task_id) and entry["log_level"] == "error" for entry in failed_calls
    )
    outage.down = False
    assert _fetch_task(outage_engine, task_id)["status"] == TaskStatus.PENDING


def test_outcome_is_recorded_once_the_database_recovers_during_the_retries(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    outage.recover_after_failures = FAILED_CALLS_BEFORE_RECOVERY

    _handler(outage_engine, RunnerThatTakesTheDatabaseDown(outage, SimulationCompleted(counts=FIRST_COUNTS))).handle(task_id)

    task = _fetch_task(outage_engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS


def test_task_arriving_while_the_database_is_unreachable_is_given_up_on_without_an_attempt(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    circuit_runner = _runner(SimulationCompleted(counts=FIRST_COUNTS))
    outage.down = True

    with pytest.raises(DatabaseUnavailable):
        _handler(outage_engine, circuit_runner).handle(task_id)

    circuit_runner.run.assert_not_called()
    outage.down = False
    task = _fetch_task(outage_engine, task_id)
    assert task["status"] == TaskStatus.PENDING
    assert task["attempts"] == 0
