# pylint: disable=invalid-name,no-member
"""Add visual_error and asr_error fields to tasks.

Revision ID: 003
Revises: 002
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add error message fields to tasks for auto-annotation failure tracking."""
    op.add_column("tasks", sa.Column("visual_error", sa.Text, nullable=True))
    op.add_column("tasks", sa.Column("asr_error", sa.Text, nullable=True))


def downgrade() -> None:
    """Remove error message fields from tasks."""
    op.drop_column("tasks", "asr_error")
    op.drop_column("tasks", "visual_error")
