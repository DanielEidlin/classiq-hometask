"""The interfaces the task handler depends on, so it doesn't depend on their implementations."""

from typing import Protocol

from worker.outcomes import SimulationOutcome


class CircuitRunner(Protocol):
    def run(self, circuit: str) -> SimulationOutcome: ...
