"""SQLAlchemy ORM models."""

# pylint: disable=too-few-public-methods
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class User(Base):
    """A corpus team member (annotator or admin)."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="annotator")
    # role: admin | annotator
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class Recording(Base):
    """A Twitch IRL recording uploaded to the corpus."""

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploading")
    # status: uploading | processing | ready | archived

    segments: Mapped[list["Segment"]] = relationship(
        "Segment", back_populates="recording", foreign_keys="Segment.recording_id"
    )


class Segment(Base):
    """A time-range slice of a Recording, either content or a gap placeholder."""

    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    recording_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recordings.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False, default="content")
    # values: content | gap
    start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # status: active | removed
    prev_segment_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("segments.id"), nullable=True
    )
    next_segment_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("segments.id"), nullable=True
    )
    split_from_segment_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("segments.id"), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    created_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    annotation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    # annotation_status: pending | in_annotation | in_validation | needs_rework | validated

    recording: Mapped["Recording"] = relationship(
        "Recording", back_populates="segments", foreign_keys=[recording_id]
    )
    tasks: Mapped[list["Task"]] = relationship(
        "Task", back_populates="segment", foreign_keys="Task.segment_id"
    )


class SegmentEvent(Base):
    """Audit-log entry for a segment lifecycle event."""

    __tablename__ = "segment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    segment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("segments.id"), nullable=False
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # event_type: created | split | removed | relinked | reannotation_triggered
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class AnnotationSchema(Base):
    """A versioned annotation schema (JSON blob) used to define EAF tiers."""

    __tablename__ = "annotation_schemas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    date: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # content: full JSON stored as string
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="schema")


class Task(Base):
    """An annotation or validation work item assigned to a team member."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    segment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("segments.id"), nullable=False
    )
    schema_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("annotation_schemas.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    # values: annotation | validation
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # status: pending | in_progress | done | approved | rejected | cancelled
    assigned_to: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    working_on: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    parent_task_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    base_eaf_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    visual_annotated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    asr_transcribed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    translated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    visual_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    asr_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    translation_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    superseded_by: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=True
    )

    segment: Mapped["Segment"] = relationship(
        "Segment", back_populates="tasks", foreign_keys=[segment_id]
    )
    schema: Mapped["AnnotationSchema"] = relationship(
        "AnnotationSchema", back_populates="tasks"
    )
    saves: Mapped[list["TaskSave"]] = relationship("TaskSave", back_populates="task")


class TaskSave(Base):
    """A saved EAF snapshot (partial or final) for a Task."""

    __tablename__ = "task_saves"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id"), nullable=False
    )
    saved_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    eaf_path: Mapped[str] = mapped_column(String(512), nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    task: Mapped["Task"] = relationship("Task", back_populates="saves")
