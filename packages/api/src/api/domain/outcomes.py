"""The stored outcome of a task."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from common.domain.tasks import TaskStatus


class PendingOutcome(BaseModel):
    status: Literal[TaskStatus.PENDING]


class CompletedOutcome(BaseModel):
    status: Literal[TaskStatus.COMPLETED]
    result: dict[str, int]


class FailedOutcome(BaseModel):
    status: Literal[TaskStatus.FAILED]
    error_message: str


# The stored state of a task. Validation picks the model by status, so a completed task
# without counts, or a failed one without a reason, is rejected here rather than served.
TaskOutcome = Annotated[
    PendingOutcome | CompletedOutcome | FailedOutcome, Field(discriminator="status")
]
