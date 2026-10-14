"""Circuit runners that act on the world while a circuit runs."""

import uuid

from fakes.database_outage import DatabaseOutage
from worker.handler import TaskHandler
from worker.outcomes import SimulationOutcome


class RunnerThatTakesTheDatabaseDown:
    """Starts the outage while the circuit runs, so the claim succeeds and the recording fails."""

    def __init__(self, outage: DatabaseOutage, outcome: SimulationOutcome) -> None:
        self._outage = outage
        self._outcome = outcome

    def run(self, circuit: str) -> SimulationOutcome:
        self._outage.down = True
        return self._outcome


class RedeliveredDuringSimulation:
    """A circuit run during which a redelivery of the same task runs to completion first,
    as when a worker is presumed lost but is still simulating."""

    def __init__(self, redelivery: TaskHandler, task_id: uuid.UUID, outcome: SimulationOutcome) -> None:
        self._redelivery = redelivery
        self._task_id = task_id
        self._outcome = outcome

    def run(self, circuit: str) -> SimulationOutcome:
        self._redelivery.handle(self._task_id)
        return self._outcome
