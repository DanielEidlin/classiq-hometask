"""The task table and the status and error-code enums shared by every service."""

import enum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


class TaskStatus(enum.StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCode(enum.StrEnum):
    INVALID_CIRCUIT = "INVALID_CIRCUIT"
    CIRCUIT_TOO_LARGE = "CIRCUIT_TOO_LARGE"
    SIMULATION_ERROR = "SIMULATION_ERROR"
    TIMEOUT = "TIMEOUT"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    WORKER_LOST = "WORKER_LOST"


def _values(enum_cls: type[enum.StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


metadata = sa.MetaData()

tasks = sa.Table(
    "tasks",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("circuit", sa.Text, nullable=False),
    sa.Column(
        "status",
        sa.Enum(TaskStatus, name="task_status", values_callable=_values),
        nullable=False,
        server_default=TaskStatus.PENDING.value,
    ),
    sa.Column("published", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("result", JSONB, nullable=True),
    sa.Column(
        "error_code",
        sa.Enum(ErrorCode, name="error_code", values_callable=_values),
        nullable=True,
    ),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
)
