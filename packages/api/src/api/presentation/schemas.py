"""The API's request and response bodies."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


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
