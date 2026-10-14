"""The interfaces the task handler depends on, so it doesn't depend on their implementations."""

import uuid
from typing import Protocol

from pydantic import BaseModel

from common.domain.tasks import TaskStatus
from worker.domain.outcomes import SimulationOutcome


class SimulationLimits(BaseModel, frozen=True):
    shots: int
    max_memory_mb: int
    timeout_seconds: float


class CircuitRunner(Protocol):
    def run(self, circuit: str) -> SimulationOutcome: ...


class DatabaseUnavailable(Exception):
    """The database stayed unreachable for the whole retry policy."""


class StartedAttempt(BaseModel):
    circuit: str
    number: int


class TaskStore(Protocol):
    """The worker's reads and writes of tasks. Every write applies only while the task is
    pending, so the first outcome recorded wins.

    Each call raises `DatabaseUnavailable` once the database has been unreachable for too long.
    """

    def start_attempt(self, task_id: uuid.UUID) -> StartedAttempt | None:
        """Count a new attempt and fetch the circuit; None when the task is final or missing."""
        ...

    def fetch_status(self, task_id: uuid.UUID) -> TaskStatus | None:
        """The task's status, or None when it doesn't exist."""
        ...

    def record_outcome(self, task_id: uuid.UUID, outcome: SimulationOutcome) -> bool:
        """Record the outcome; False when another attempt already recorded one."""
        ...
