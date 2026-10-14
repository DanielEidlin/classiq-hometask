"""An in-memory stand-in for the worker's task store."""

import uuid

from common.tasks import TaskStatus
from worker.abstractions import DatabaseUnavailable, StartedAttempt
from worker.outcomes import SimulationOutcome


class FakeTaskStore:
    """Answers with what the test arranges and keeps the outcomes recorded through it.

    It doesn't imitate the database: the conditional writes are tested on the real store.
    """

    def __init__(
        self,
        started: StartedAttempt | None = None,
        status: TaskStatus | None = None,
        outcome_is_first: bool = True,
        unavailable: bool = False,
    ) -> None:
        self._started = started
        self._status = status
        self._outcome_is_first = outcome_is_first
        self._unavailable = unavailable
        self.recorded: list[SimulationOutcome] = []

    def start_attempt(self, task_id: uuid.UUID) -> StartedAttempt | None:
        self._raise_if_unavailable()
        return self._started

    def fetch_status(self, task_id: uuid.UUID) -> TaskStatus | None:
        self._raise_if_unavailable()
        return self._status

    def record_outcome(self, task_id: uuid.UUID, outcome: SimulationOutcome) -> bool:
        self._raise_if_unavailable()
        self.recorded.append(outcome)
        return self._outcome_is_first

    def _raise_if_unavailable(self) -> None:
        if self._unavailable:
            raise DatabaseUnavailable
