"""The API's request and response bodies, and the stored outcome of a task."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from common.tasks import TaskStatus


class SubmitTaskRequest(BaseModel):
    quantum_circuit: str = Field(
        alias="qc", min_length=1, description="The circuit, as QASM3 text."
    )


class SubmitTaskResponse(BaseModel):
    task_id: uuid.UUID
    message: Literal["Task submitted successfully."] = "Task submitted successfully."


class PendingTaskResponse(BaseModel):
    status: Literal["pending"] = "pending"
    message: Literal["Task is still in progress."] = "Task is still in progress."


class CompletedTaskResponse(BaseModel):
    status: Literal["completed"] = "completed"
    result: dict[str, int]


class FailedTaskResponse(BaseModel):
    status: Literal["error"] = "error"
    message: str


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
