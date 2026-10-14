"""The task status and error-code enums shared by every service."""

import enum


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCode(enum.StrEnum):
    INVALID_CIRCUIT = "INVALID_CIRCUIT"
    CIRCUIT_TOO_LARGE = "CIRCUIT_TOO_LARGE"
    SIMULATION_ERROR = "SIMULATION_ERROR"
    TIMEOUT = "TIMEOUT"
    KILLED = "KILLED"
    WORKER_LOST = "WORKER_LOST"
