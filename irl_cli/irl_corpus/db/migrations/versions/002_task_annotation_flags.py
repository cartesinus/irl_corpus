# pylint: disable=invalid-name,no-member
"""Add visual_annotated and asr_transcribed flags to tasks.

Revision ID: 002
Revises: 001
Create Date: 2026-05-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add auto-annotation state flags to tasks."""
    op.add_column(
        "tasks",
        sa.Column("visual_annotated", sa.Boolean, nullable=False, server_default="0"),
    )
    op.add_column(
        "tasks",
        sa.Column("asr_transcribed", sa.Boolean, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Remove auto-annotation state flags from tasks."""
    op.drop_column("tasks", "asr_transcribed")
    op.drop_column("tasks", "visual_annotated")
