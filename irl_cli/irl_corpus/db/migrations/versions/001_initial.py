# pylint: disable=invalid-name,no-member
"""Initial schema: all tables.

Revision ID: 001
Revises:
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="annotator"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "recordings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("original_filename", sa.String(256), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column(
            "uploaded_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="uploading"),
    )

    op.create_table(
        "segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "recording_id",
            sa.String(36),
            sa.ForeignKey("recordings.id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False, server_default="content"),
        sa.Column("start_ms", sa.Integer, nullable=True),
        sa.Column("end_ms", sa.Integer, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "prev_segment_id",
            sa.String(36),
            sa.ForeignKey("segments.id"),
            nullable=True,
        ),
        sa.Column(
            "next_segment_id",
            sa.String(36),
            sa.ForeignKey("segments.id"),
            nullable=True,
        ),
        sa.Column(
            "split_from_segment_id",
            sa.String(36),
            sa.ForeignKey("segments.id"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "annotation_status", sa.String(32), nullable=False, server_default="pending"
        ),
    )

    op.create_table(
        "segment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "segment_id", sa.String(36), sa.ForeignKey("segments.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.Text, nullable=False, server_default="{}"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "annotation_schemas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("version", sa.String(32), nullable=False, unique=True),
        sa.Column("date", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default="0"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "segment_id", sa.String(36), sa.ForeignKey("segments.id"), nullable=False
        ),
        sa.Column(
            "schema_id",
            sa.String(36),
            sa.ForeignKey("annotation_schemas.id"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "assigned_to", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "working_on", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "parent_task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True
        ),
        sa.Column("round", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("base_eaf_path", sa.String(512), nullable=True),
        sa.Column(
            "superseded_by", sa.String(36), sa.ForeignKey("tasks.id"), nullable=True
        ),
    )

    op.create_table(
        "task_saves",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("saved_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eaf_path", sa.String(512), nullable=False),
        sa.Column("is_final", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("notes", sa.Text, nullable=True),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("task_saves")
    op.drop_table("tasks")
    op.drop_table("annotation_schemas")
    op.drop_table("segment_events")
    op.drop_table("segments")
    op.drop_table("recordings")
    op.drop_table("users")
