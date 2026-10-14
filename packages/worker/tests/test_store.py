"""The worker's task store against a real Postgres: its statements, and its retries while the
database is unreachable."""

import uuid
from collections.abc import Callable, Iterator

import pytest
import sqlalchemy as sa
import tenacity
from structlog.testing import capture_logs

from common.tasks import ErrorCode, TaskStatus, tasks
from fakes.database_outage import DatabaseOutage
from worker.abstractions import DatabaseUnavailable, StartedAttempt
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.store import PostgresTaskStore, create_database_engine, retry_while_unavailable

CIRCUIT = "OPENQASM 3.0;"
FIRST_COUNTS = {"00": 1024}
SECOND_COUNTS = {"11": 1024}
FIRST_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 30.0
GIVE_UP_SECONDS = 240.0
# Enough failed calls to show the doubling, the cap, and the delays after it.
FAILED_CALLS_SHOWN = 8
# The outage tests give up after this many failed calls instead of after a delay.
FAILED_CALLS_BEFORE_GIVING_UP = 4
# The backoff between those four failed calls.
DELAYS_UNTIL_GIVING_UP = [1.0, 2.0, 4.0]
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


def _retrying() -> tenacity.Retrying:
    return retry_while_unavailable(FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS, GIVE_UP_SECONDS)


def _discard_sleep(seconds: float) -> None:
    return None


def _store(engine: sa.Engine, sleep: Callable[[float], None] = _discard_sleep) -> PostgresTaskStore:
    """The production retry and backoff, giving up after a number of failed calls rather
    than after a delay, so that sleeping can be skipped."""
    retrying = _retrying().copy(
        stop=tenacity.stop_after_attempt(FAILED_CALLS_BEFORE_GIVING_UP), sleep=sleep
    )
    return PostgresTaskStore(engine, retrying)


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


def test_calls_are_retried_then_given_up_on_while_the_database_stays_unreachable(
    outage_engine: sa.Engine,
) -> None:
    task_id = _insert_task(outage_engine)
    outage = DatabaseOutage(outage_engine)
    sleeps: list[float] = []
    store = _store(outage_engine, sleeps.append)
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


def _retry_state(retrying: tenacity.Retrying, failed_calls: int) -> tenacity.RetryCallState:
    retry_state = tenacity.RetryCallState(retry_object=retrying, fn=None, args=(), kwargs={})
    retry_state.attempt_number = failed_calls
    return retry_state


def test_backoff_doubles_until_it_reaches_the_cap_and_then_stays_there() -> None:
    retrying = _retrying()

    delays = [
        retrying.wait(_retry_state(retrying, failed_calls))
        for failed_calls in range(1, FAILED_CALLS_SHOWN + 1)
    ]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


@pytest.mark.parametrize(
    ("seconds_since_start", "upcoming_sleep", "gives_up"),
    [
        pytest.param(200.0, 30.0, False, id="the next sleep ends before the give-up time"),
        pytest.param(220.0, 30.0, True, id="the next sleep would pass the give-up time"),
    ],
)
def test_retrying_gives_up_rather_than_sleep_past_the_give_up_time(
    seconds_since_start: float, upcoming_sleep: float, gives_up: bool
) -> None:
    retrying = _retrying()
    retry_state = _retry_state(retrying, failed_calls=1)
    retry_state.outcome_timestamp = retry_state.start_time + seconds_since_start
    retry_state.upcoming_sleep = upcoming_sleep

    assert retrying.stop(retry_state) is gives_up


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(sa.exc.OperationalError("", {}, Exception()), id="unreachable"),
        pytest.param(sa.exc.InterfaceError("", {}, Exception()), id="connection closed"),
        pytest.param(sa.exc.TimeoutError(), id="pool exhausted"),
    ],
)
def test_unreachable_database_errors_are_retried(error: Exception) -> None:
    retrying = _retrying()
    retry_state = _retry_state(retrying, failed_calls=1)
    retry_state.set_exception((type(error), error, None))

    assert retrying.retry(retry_state)


def test_other_database_errors_are_not_retried() -> None:
    retrying = _retrying()
    retry_state = _retry_state(retrying, failed_calls=1)
    error = sa.exc.IntegrityError("", {}, Exception())
    retry_state.set_exception((type(error), error, None))

    assert not retrying.retry(retry_state)


def test_database_calls_time_out_instead_of_hanging(db_engine: sa.Engine) -> None:
    engine = create_database_engine(db_engine.url.render_as_string(hide_password=False), DB_TIMEOUT_SECONDS)
    try:
        with pytest.raises(sa.exc.OperationalError), engine.connect() as connection:
            connection.execute(sa.select(sa.func.pg_sleep(QUERY_SECONDS)))
    finally:
        engine.dispose()
