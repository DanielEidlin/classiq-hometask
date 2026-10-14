"""The worker's task handler against the stack's Postgres, with the simulation replaced.

Duplicate handling is tested here rather than end to end: arranging an overlapping
redelivery through the queue would depend on timing, while calling the handler is
deterministic.
"""

import uuid
from typing import Any
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from circuits import BELL_CIRCUIT
from common.tasks import ErrorCode, TaskStatus, tasks
from worker.handler import WORKER_LOST_MESSAGE, TaskHandler
from worker.outcomes import SimulationCompleted, SimulationFailed, SimulationOutcome

pytestmark = pytest.mark.integration

MAX_ATTEMPTS = 3
FIRST_COUNTS = {"00": 1024}
SECOND_COUNTS = {"11": 1024}


def _insert_task(engine: sa.Engine, **values: Any) -> uuid.UUID:
    # Marked published, so the stack's sweeper never hands it to the real workers.
    task_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            tasks.insert().values(id=task_id, circuit=BELL_CIRCUIT, published=True, **values)
        )
    return task_id


def _fetch_task(engine: sa.Engine, task_id: uuid.UUID) -> sa.RowMapping:
    with engine.connect() as connection:
        return connection.execute(sa.select(tasks).where(tasks.c.id == task_id)).mappings().one()


def _simulation(outcome: SimulationOutcome) -> Mock:
    return Mock(return_value=outcome)


class _RedeliveredDuringSimulation:
    """A simulation during which a redelivery of the same task runs to completion first,
    as when a worker is presumed lost but is still simulating."""

    def __init__(self, engine: sa.Engine, task_id: uuid.UUID) -> None:
        self._engine = engine
        self._task_id = task_id

    def __call__(self, circuit: str) -> SimulationOutcome:
        redelivery = TaskHandler(
            self._engine, _simulation(SimulationCompleted(counts=FIRST_COUNTS)), MAX_ATTEMPTS
        )
        redelivery.handle(self._task_id)
        return SimulationCompleted(counts=SECOND_COUNTS)


def test_running_a_task_twice_keeps_the_first_result(db_engine: sa.Engine) -> None:
    task_id = _insert_task(db_engine)
    handler = TaskHandler(
        db_engine, _RedeliveredDuringSimulation(db_engine, task_id), MAX_ATTEMPTS
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

    TaskHandler(db_engine, _simulation(outcome), MAX_ATTEMPTS).handle(task_id)

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

    TaskHandler(db_engine, simulation, MAX_ATTEMPTS).handle(task_id)

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

    TaskHandler(db_engine, simulation, MAX_ATTEMPTS).handle(task_id)

    simulation.assert_not_called()
    assert _fetch_task(db_engine, task_id) == before
