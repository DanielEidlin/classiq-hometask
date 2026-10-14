"""The worker's task store against a real Postgres: its statements, and its retries while the
database is unreachable."""

import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from common.tasks import ErrorCode, TaskStatus, tasks
from fakes.database_outage import DatabaseOutage
from worker.abstractions import DatabaseUnavailable, StartedAttempt
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.store import DatabaseRetryPolicy, PostgresTaskStore, create_database_engine

CIRCUIT = "OPENQASM 3.0;"
FIRST_COUNTS = {"00": 1024}
SECOND_COUNTS = {"11": 1024}
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
# The tests' sleeps don't advance the clock, so the store gives up once the next delay alone
# would pass the give-up time: after the delays of 1, 2 and 4 seconds, as the next is 8.
GIVE_UP_SECONDS = 5.0
POLICY = DatabaseRetryPolicy(
    first_delay_seconds=FIRST_DELAY_SECONDS,
    max_delay_seconds=MAX_DELAY_SECONDS,
    give_up_seconds=GIVE_UP_SECONDS,
)
DELAYS_UNTIL_GIVING_UP = [1.0, 2.0, 4.0]
FAILED_CALLS_BEFORE_GIVING_UP = len(DELAYS_UNTIL_GIVING_UP) + 1
# Past the delay cap, so the backoff can be seen reaching it; the outage ends the retries.
POLICY_PAST_THE_CAP = POLICY.model_copy(update={"give_up_seconds": 2 * MAX_DELAY_SECONDS})
DELAYS_UP_TO_THE_CAP = [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]
FAILED_CALLS_BEFORE_RECOVERY = 2
DB_TIMEOUT_SECONDS = 1
# Longer than DB_TIMEOUT_SECONDS, so the query only returns if nothing times it out.
QUERY_SECONDS = 30


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


def _store(
    engine: sa.Engine, sleeps: list[float] | None = None, policy: DatabaseRetryPolicy = POLICY
) -> PostgresTaskStore:
    """A store whose sleeps between retries are recorded in `sleeps` instead of waited out."""
    return PostgresTaskStore(engine, policy, sleep=(sleeps if sleeps is not None else []).append)


@pytest.fixture
def outage_engine(db_engine: sa.Engine) -> Iterator[sa.Engine]:
    """An engine of its own on the test database, so an outage's listener doesn't outlive the test."""
    engine = sa.create_engine(db_engine.url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_starting_an_attempt_counts_it_and_returns_the_circuit(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine, attempts=1)

    started = _store(db_engine).start_attempt(task_id)

    assert started == StartedAttempt(circuit=CIRCUIT, number=2)
    task = _fetch_task(db_engine, task_id)
    assert task["attempts"] == 2
    assert task["started_at"] is not None


def test_no_attempt_starts_on_a_final_task(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine, status=TaskStatus.COMPLETED, attempts=1, result=FIRST_COUNTS)

    assert _store(db_engine).start_attempt(task_id) is None
    assert _fetch_task(db_engine, task_id)["attempts"] == 1


def test_no_attempt_starts_on_a_missing_task(db_engine: sa.Engine) -> None:
    assert _store(db_engine).start_attempt(uuid.uuid4()) is None


def test_status_is_fetched_and_none_for_a_missing_task(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine, status=TaskStatus.COMPLETED, result=FIRST_COUNTS)
    store = _store(db_engine)

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
    db_engine: sa.Engine, outcome: SimulationOutcome
) -> None:
    task_id = _insert_task(db_engine)

    assert _store(db_engine).record_outcome(task_id, outcome)

    task = _fetch_task(db_engine, task_id)
    assert task["status"] == outcome.status
    assert task["finished_at"] is not None


def test_failure_is_recorded_with_its_code_and_message(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine)
    failure = SimulationFailed(code=ErrorCode.TIMEOUT, message="Simulation exceeded the time limit.")

    _store(db_engine).record_outcome(task_id, failure)

    task = _fetch_task(db_engine, task_id)
    assert task["error_code"] == ErrorCode.TIMEOUT
    assert task["error_message"] == failure.message


def test_a_second_outcome_is_discarded_and_the_first_kept(db_engine: sa.Engine) -> None:
    """Two overlapping attempts of one task, as after a redelivery: the first to finish wins."""
    task_id = _insert_task(db_engine)
    store = _store(db_engine)
    store.start_attempt(task_id)
    store.start_attempt(task_id)

    first = store.record_outcome(task_id, SimulationCompleted(counts=FIRST_COUNTS))
    second = store.record_outcome(task_id, SimulationCompleted(counts=SECOND_COUNTS))

    assert (first, second) == (True, False)
    task = _fetch_task(db_engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS
    assert task["attempts"] == 2




def test_calls_are_retried_with_backoff_then_given_up_on_while_the_database_stays_unreachable(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    sleeps: list[float] = []
    store = _store(outage_engine, sleeps)
    outage.down = True

    with capture_logs() as logs, pytest.raises(DatabaseUnavailable):
        store.record_outcome(task_id, SimulationCompleted(counts=FIRST_COUNTS))

    assert sleeps == DELAYS_UNTIL_GIVING_UP
    failed_calls = [entry for entry in logs if entry["event"] == "database_call_failed"]
    assert len(failed_calls) == FAILED_CALLS_BEFORE_GIVING_UP
    assert all(
        entry["task_id"] == str(task_id) and entry["log_level"] == "error" for entry in failed_calls
    )
    outage.down = False
    assert _fetch_task(outage_engine, task_id)["status"] == TaskStatus.PENDING


def test_backoff_doubles_until_it_reaches_the_cap_and_then_stays_there(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    outage.recover_after_failures = len(DELAYS_UP_TO_THE_CAP)
    outage.down = True
    sleeps: list[float] = []

    _store(outage_engine, sleeps, POLICY_PAST_THE_CAP).fetch_status(task_id)

    assert sleeps == DELAYS_UP_TO_THE_CAP


def test_outcome_is_recorded_once_the_database_recovers_during_the_retries(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    outage.recover_after_failures = FAILED_CALLS_BEFORE_RECOVERY
    outage.down = True

    assert _store(outage_engine).record_outcome(task_id, SimulationCompleted(counts=FIRST_COUNTS))

    task = _fetch_task(outage_engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS


def test_no_attempt_is_counted_while_the_database_is_unreachable(outage_engine: sa.Engine) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    outage.down = True

    with pytest.raises(DatabaseUnavailable):
        _store(outage_engine).start_attempt(task_id)

    outage.down = False
    assert _fetch_task(outage_engine, task_id)["attempts"] == 0


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(sa.exc.OperationalError("", {}, Exception()), id="unreachable"),
        pytest.param(sa.exc.InterfaceError("", {}, Exception()), id="connection closed"),
        pytest.param(sa.exc.TimeoutError(), id="pool exhausted"),
    ],
)
def test_unreachable_database_errors_are_retried(
    outage_engine: sa.Engine, error: Exception
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine, error)
    outage.recover_after_failures = 1
    outage.down = True
    sleeps: list[float] = []

    assert _store(outage_engine, sleeps).fetch_status(task_id) == TaskStatus.PENDING
    assert sleeps == [FIRST_DELAY_SECONDS]


def test_other_database_errors_are_raised_without_a_retry(outage_engine: sa.Engine) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine, sa.exc.IntegrityError("", {}, Exception()))
    outage.down = True
    sleeps: list[float] = []

    with pytest.raises(sa.exc.IntegrityError):
        _store(outage_engine, sleeps).fetch_status(task_id)

    assert sleeps == []


def test_database_calls_time_out_instead_of_hanging(db_engine: sa.Engine) -> None:
    engine = create_database_engine(
        db_engine.url.render_as_string(hide_password=False), DB_TIMEOUT_SECONDS
    )
    try:
        with pytest.raises(sa.exc.OperationalError), engine.connect() as connection:
            connection.execute(sa.select(sa.func.pg_sleep(QUERY_SECONDS)))
    finally:
        engine.dispose()
