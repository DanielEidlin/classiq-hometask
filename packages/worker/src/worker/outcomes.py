"""What a simulation produces: counts, or a failure with a code and a client-facing reason."""

from typing import Literal

from pydantic import BaseModel

from common.tasks import ErrorCode


class SimulationCompleted(BaseModel):
    status: Literal["completed"] = "completed"
    counts: dict[str, int]


class SimulationFailed(BaseModel):
    status: Literal["failed"] = "failed"
    code: ErrorCode
    message: str
    # For logs only, never returned to clients.
    traceback: str | None = None


SimulationOutcome = SimulationCompleted | SimulationFailed
