"""Create the tasks table.

Revision ID: 0001
Revises:
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

task_status = postgresql.ENUM("pending", "completed", "failed", name="task_status")
error_code = postgresql.ENUM(
    "INVALID_CIRCUIT",
    "CIRCUIT_TOO_LARGE",
    "SIMULATION_ERROR",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "WORKER_LOST",
    name="error_code",
)


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("circuit", sa.Text, nullable=False),
        sa.Column("status", task_status, nullable=False, server_default="pending"),
        sa.Column("published", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("result", postgresql.JSONB, nullable=True),
        sa.Column("error_code", error_code, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("tasks")
    error_code.drop(op.get_bind())
    task_status.drop(op.get_bind())
