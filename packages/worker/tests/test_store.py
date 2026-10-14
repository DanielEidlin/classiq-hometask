"""The worker's task store against a real Postgres: its statements, and its retries while the
database is unreachable."""

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
import tenacity
from structlog.testing import capture_logs

from common.tasks import ErrorCode, TaskStatus, tasks
from fakes.database_outage import DatabaseOutage
from worker.abstractions import DatabaseUnavailable, StartedAttempt
from worker.database import retry_while_unavailable
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.store import PostgresTaskStore

CIRCUIT = "OPENQASM 3.0;"
FIRST_COUNTS = {"00": 1024}
SECOND_COUNTS = {"11": 1024}
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
GIVE_UP_SECONDS = 240.0
# The outage tests give up after this many failed calls instead of after a delay.
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
        id=task_id, circuit=CIRCUIT, status=status, attempts=attempts, result=result
    )
    with engine.begin() as connection:
        connection.execute(statement)
    return task_id


def _fetch_task(engine: sa.Engine, task_id: uuid.UUID) -> sa.RowMapping:
    with engine.connect() as connection:
        return connection.execute(sa.select(tasks).where(tasks.c.id == task_id)).mappings().one()


def _retrying() -> tenacity.Retrying:
    return retry_while_unavailable(FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS, GIVE_UP_SECONDS)


@pytest.fixture
def outage_engine(db_engine: sa.Engine) -> Iterator[sa.Engine]:
    """An engine of its own on the test database, so an outage's listener doesn't outlive the test."""
    engine = sa.create_engine(db_engine.url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def store(db_engine: sa.Engine) -> PostgresTaskStore:
    return PostgresTaskStore(db_engine, _retrying())


@pytest.fixture
def outage(outage_engine: sa.Engine) -> DatabaseOutage:
    return DatabaseOutage(outage_engine)


@pytest.fixture
def sleeps() -> list[float]:
    """The delays the outage store waited between retries, recorded instead of slept."""
    return []


@pytest.fixture
def outage_store(outage_engine: sa.Engine, sleeps: list[float]) -> PostgresTaskStore:
    """The production retry and backoff on the outage's engine, giving up after a number of
    failed calls rather than after a delay, so that sleeping can be skipped."""
    retrying = _retrying().copy(
        stop=tenacity.stop_after_attempt(FAILED_CALLS_BEFORE_GIVING_UP), sleep=sleeps.append
    )
    return PostgresTaskStore(outage_engine, retrying)


def test_starting_an_attempt_counts_it_and_returns_the_circuit(
    db_engine: sa.Engine, store: PostgresTaskStore
) -> None:
    task_id = _insert_task(db_engine, attempts=1)

    started = store.start_attempt(task_id)

    assert started == StartedAttempt(circuit=CIRCUIT, number=2)
    task = _fetch_task(db_engine, task_id)
    assert task["attempts"] == 2
    assert task["started_at"] is not None


def test_no_attempt_starts_on_a_final_task(db_engine: sa.Engine, store: PostgresTaskStore) -> None:
    task_id = _insert_task(db_engine, status=TaskStatus.COMPLETED, attempts=1, result=FIRST_COUNTS)

    assert store.start_attempt(task_id) is None
    assert _fetch_task(db_engine, task_id)["attempts"] == 1


def test_no_attempt_starts_on_a_missing_task(store: PostgresTaskStore) -> None:
    assert store.start_attempt(uuid.uuid4()) is None


def test_status_is_fetched_and_none_for_a_missing_task(
    db_engine: sa.Engine, store: PostgresTaskStore
) -> None:
    task_id = _insert_task(db_engine, status=TaskStatus.COMPLETED, result=FIRST_COUNTS)

    assert store.fetch_status(task_id) == TaskStatus.COMPLETED
    assert store.fetch_status(uuid.uuid4()) is None


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
    db_engine: sa.Engine, store: PostgresTaskStore, outcome: SimulationOutcome
) -> None:
    task_id = _insert_task(db_engine)

    assert store.record_outcome(task_id, outcome)

    task = _fetch_task(db_engine, task_id)
    assert task["status"] == outcome.status
    assert task["finished_at"] is not None


def test_failure_is_recorded_with_its_code_and_message(
    db_engine: sa.Engine, store: PostgresTaskStore
) -> None:
    task_id = _insert_task(db_engine)
    failure = SimulationFailed(code=ErrorCode.TIMEOUT, message="Simulation exceeded the time limit.")

    store.record_outcome(task_id, failure)

    task = _fetch_task(db_engine, task_id)
    assert task["error_code"] == ErrorCode.TIMEOUT
    assert task["error_message"] == failure.message


def test_a_second_outcome_is_discarded_and_the_first_kept(
    db_engine: sa.Engine, store: PostgresTaskStore
) -> None:
    """Two overlapping attempts of one task, as after a redelivery: the first to finish wins."""
    task_id = _insert_task(db_engine)
    store.start_attempt(task_id)
    store.start_attempt(task_id)

    first = store.record_outcome(task_id, SimulationCompleted(counts=FIRST_COUNTS))
    second = store.record_outcome(task_id, SimulationCompleted(counts=SECOND_COUNTS))

    assert (first, second) == (True, False)
    task = _fetch_task(db_engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS
    assert task["attempts"] == 2


def test_calls_are_retried_then_given_up_on_while_the_database_stays_unreachable(
    outage_engine: sa.Engine,
    outage: DatabaseOutage,
    outage_store: PostgresTaskStore,
    sleeps: list[float],
) -> None:
    task_id = _insert_task(outage_engine)
    outage.down = True

    with capture_logs() as logs, pytest.raises(DatabaseUnavailable):
        outage_store.record_outcome(task_id, SimulationCompleted(counts=FIRST_COUNTS))

    assert sleeps == DELAYS_UNTIL_GIVING_UP
    failed_calls = [entry for entry in logs if entry["event"] == "database_call_failed"]
    assert len(failed_calls) == FAILED_CALLS_BEFORE_GIVING_UP
    assert all(
        entry["task_id"] == str(task_id) and entry["log_level"] == "error" for entry in failed_calls
    )
    outage.down = False
    assert _fetch_task(outage_engine, task_id)["status"] == TaskStatus.PENDING


def test_outcome_is_recorded_once_the_database_recovers_during_the_retries(
    outage_engine: sa.Engine, outage: DatabaseOutage, outage_store: PostgresTaskStore
) -> None:
    task_id = _insert_task(outage_engine)
    outage.recover_after_failures = FAILED_CALLS_BEFORE_RECOVERY
    outage.down = True

    assert outage_store.record_outcome(task_id, SimulationCompleted(counts=FIRST_COUNTS))

    task = _fetch_task(outage_engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS


def test_no_attempt_is_counted_while_the_database_is_unreachable(
    outage_engine: sa.Engine, outage: DatabaseOutage, outage_store: PostgresTaskStore
) -> None:
    task_id = _insert_task(outage_engine)
    outage.down = True

    with pytest.raises(DatabaseUnavailable):
        outage_store.start_attempt(task_id)

    outage.down = False
    assert _fetch_task(outage_engine, task_id)["attempts"] == 0
