"""Rename the OUT_OF_MEMORY error code to KILLED.

A child killed by a signal is usually, but not certainly, out of memory.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE error_code RENAME VALUE 'OUT_OF_MEMORY' TO 'KILLED'")


def downgrade() -> None:
    op.execute("ALTER TYPE error_code RENAME VALUE 'KILLED' TO 'OUT_OF_MEMORY'")
