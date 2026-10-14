"""The worker's task handler against a real Postgres, with the simulation replaced.

Duplicate handling is tested here rather than end to end: arranging an overlapping
redelivery through the queue would depend on timing, while calling the handler is
deterministic.
"""

import functools
import uuid
from collections.abc import Iterator
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from common.tasks import ErrorCode, TaskStatus, tasks
from worker.database import create_db_engine
from worker.handler import WORKER_LOST_MESSAGE, DatabaseUnavailable, Simulation, TaskHandler
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome
from worker.retry import RetryPolicy

MAX_ATTEMPTS = 3
# Never simulated: every test replaces the simulation.
CIRCUIT = "OPENQASM 3.0;"
FIRST_COUNTS = {"00": 1024}
SECOND_COUNTS = {"11": 1024}
RETRY_POLICY = RetryPolicy(first_delay_seconds=1, max_delay_seconds=4, give_up_seconds=10)
# RETRY_POLICY's backoff 1, 2, 4, 4, ... with the last delay cut to what's left of the 10 s.
DELAYS_UNTIL_GIVING_UP = [1.0, 2.0, 4.0, 3.0]
SLEEPS_BEFORE_RECOVERY = 2
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


def _simulation(outcome: SimulationOutcome) -> Mock:
    return Mock(return_value=outcome)


def _handler(engine: sa.Engine, simulation: Simulation) -> TaskHandler:
    return TaskHandler(engine, simulation, MAX_ATTEMPTS, RETRY_POLICY)


class _DatabaseOutage:
    """An engine on the test database whose statements fail while it is down, as an
    unreachable database's would, and a clock that advances only when the handler sleeps."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine
        self.down = False
        self.recover_after_sleeps: int | None = None
        self.sleeps: list[float] = []
        self._now = 0.0
        sa.event.listen(engine, "before_cursor_execute", self._fail_while_down)

    def _fail_while_down(self, *args: object) -> None:
        if self.down:
            raise sa.exc.OperationalError("", {}, ConnectionRefusedError("connection refused"))

    def _monotonic(self) -> float:
        return self._now

    def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds
        if len(self.sleeps) == self.recover_after_sleeps:
            self.down = False

    def go_down_during_simulation(self, outcome: SimulationOutcome, circuit: str) -> SimulationOutcome:
        self.down = True
        return outcome

    def handler(self, simulation: Simulation) -> TaskHandler:
        return TaskHandler(
            self.engine, simulation, MAX_ATTEMPTS, RETRY_POLICY,
            sleep=self._sleep, clock=self._monotonic,
        )


@pytest.fixture
def outage(db_engine: sa.Engine) -> Iterator[_DatabaseOutage]:
    engine = sa.create_engine(db_engine.url)
    try:
        yield _DatabaseOutage(engine)
    finally:
        engine.dispose()


class _RedeliveredDuringSimulation:
    """A simulation during which a redelivery of the same task runs to completion first,
    as when a worker is presumed lost but is still simulating."""

    def __init__(self, engine: sa.Engine, task_id: uuid.UUID) -> None:
        self._engine = engine
        self._task_id = task_id

    def __call__(self, circuit: str) -> SimulationOutcome:
        redelivery = _handler(self._engine, _simulation(SimulationCompleted(counts=FIRST_COUNTS)))
        redelivery.handle(self._task_id)
        return SimulationCompleted(counts=SECOND_COUNTS)


def test_running_a_task_twice_keeps_the_first_result(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine)
    handler = _handler(db_engine, _RedeliveredDuringSimulation(db_engine, task_id))

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

    _handler(db_engine, _simulation(outcome)).handle(task_id)

    task = _fetch_task(db_engine, task_id)
    assert task["status"] == outcome.status
    assert task["attempts"] == 1
    assert task["started_at"] is not None
    assert task["finished_at"] is not None


def test_task_at_the_attempt_cap_fails_with_worker_lost_without_simulating(
    db_engine: sa.Engine,
) -> None:
    task_id = _insert_task(db_engine, attempts=MAX_ATTEMPTS)
    simulation = _simulation(SimulationCompleted(counts=FIRST_COUNTS))

    _handler(db_engine, simulation).handle(task_id)

    simulation.assert_not_called()
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
    simulation = _simulation(SimulationCompleted(counts=SECOND_COUNTS))

    with capture_logs() as logs:
        _handler(db_engine, simulation).handle(task_id)

    simulation.assert_not_called()
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
    simulation = _simulation(SimulationCompleted(counts=FIRST_COUNTS))

    with capture_logs() as logs:
        _handler(db_engine, simulation).handle(task_id)

    simulation.assert_not_called()
    assert {
        "event": "task_skipped",
        "task_id": str(task_id),
        "reason": "not found",
        "log_level": "info",
    } in logs


def test_outcome_is_given_up_on_for_requeue_while_the_database_stays_unreachable(
    outage: _DatabaseOutage,
) -> None:
    task_id = _insert_task(outage.engine)
    simulation = functools.partial(
        outage.go_down_during_simulation, SimulationCompleted(counts=FIRST_COUNTS)
    )

    with capture_logs() as logs, pytest.raises(DatabaseUnavailable):
        outage.handler(simulation).handle(task_id)

    assert outage.sleeps == DELAYS_UNTIL_GIVING_UP
    failed_calls = [entry for entry in logs if entry["event"] == "database_call_failed"]
    assert len(failed_calls) == len(DELAYS_UNTIL_GIVING_UP) + 1
    assert all(
        entry["task_id"] == str(task_id) and entry["log_level"] == "error" for entry in failed_calls
    )
    outage.down = False
    assert _fetch_task(outage.engine, task_id)["status"] == TaskStatus.PENDING


def test_outcome_is_recorded_once_the_database_recovers_during_the_retries(
    outage: _DatabaseOutage,
) -> None:
    task_id = _insert_task(outage.engine)
    outage.recover_after_sleeps = SLEEPS_BEFORE_RECOVERY
    simulation = functools.partial(
        outage.go_down_during_simulation, SimulationCompleted(counts=FIRST_COUNTS)
    )

    outage.handler(simulation).handle(task_id)

    assert len(outage.sleeps) == SLEEPS_BEFORE_RECOVERY
    task = _fetch_task(outage.engine, task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["result"] == FIRST_COUNTS


def test_task_arriving_while_the_database_is_unreachable_is_given_up_on_without_an_attempt(
    outage: _DatabaseOutage,
) -> None:
    task_id = _insert_task(outage.engine)
    simulation = _simulation(SimulationCompleted(counts=FIRST_COUNTS))
    outage.down = True

    with pytest.raises(DatabaseUnavailable):
        outage.handler(simulation).handle(task_id)

    simulation.assert_not_called()
    assert outage.sleeps == DELAYS_UNTIL_GIVING_UP
    outage.down = False
    task = _fetch_task(outage.engine, task_id)
    assert task["status"] == TaskStatus.PENDING
    assert task["attempts"] == 0


def test_database_calls_time_out_instead_of_hanging(db_engine: sa.Engine) -> None:
    engine = create_db_engine(db_engine.url.render_as_string(hide_password=False), DB_TIMEOUT_SECONDS)
    try:
        with pytest.raises(sa.exc.OperationalError), engine.connect() as connection:
            connection.execute(sa.select(sa.func.pg_sleep(QUERY_SECONDS)))
    finally:
        engine.dispose()
