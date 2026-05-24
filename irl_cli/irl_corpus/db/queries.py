"""Common query helpers."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AnnotationSchema, Recording, Segment, Task, TaskSave, User


# ── Users ─────────────────────────────────────────────────────────────────────


def get_user(session: Session, name: str) -> User | None:
    """Return a User by name, or None if not found."""
    return session.query(User).filter_by(name=name).first()


def require_user(session: Session, name: str) -> User:
    """Return a User by name, raising ValueError if not found."""
    user = get_user(session, name)
    if not user:
        raise ValueError(
            f"User '{name}' not found. Add them first: irl user add --name {name}"
        )
    return user


def list_users(session: Session) -> list[User]:
    """Return all users ordered by creation date."""
    return session.query(User).order_by(User.created_at).all()


# ── Schemas ───────────────────────────────────────────────────────────────────


def get_current_schema(session: Session) -> AnnotationSchema | None:
    """Return the currently active annotation schema, or None."""
    return session.query(AnnotationSchema).filter_by(is_current=True).first()


def require_current_schema(session: Session) -> AnnotationSchema:
    """Return the current annotation schema, raising ValueError if unset."""
    schema = get_current_schema(session)
    if not schema:
        raise ValueError(
            "No active annotation schema. Import one first: irl schema import <file.json>"
        )
    return schema


def get_schema_by_version(session: Session, version: str) -> AnnotationSchema | None:
    """Return an AnnotationSchema by version string, or None."""
    return session.query(AnnotationSchema).filter_by(version=version).first()


def list_schemas(session: Session) -> list[AnnotationSchema]:
    """Return all annotation schemas ordered by import date."""
    return session.query(AnnotationSchema).order_by(AnnotationSchema.imported_at).all()


# ── Recordings ────────────────────────────────────────────────────────────────


def get_recording(session: Session, recording_id: str) -> Recording | None:
    """Return a Recording by exact ID or unique prefix, or None."""
    rec = session.get(Recording, recording_id)
    if rec:
        return rec
    matches = (
        session.query(Recording).filter(Recording.id.like(f"{recording_id}%")).all()
    )
    return matches[0] if len(matches) == 1 else None


def require_recording(session: Session, recording_id: str) -> Recording:
    """Return a Recording by ID or prefix, raising ValueError if not found."""
    rec = get_recording(session, recording_id)
    if not rec:
        raise ValueError(f"Recording '{recording_id}' not found.")
    return rec


def list_recordings(session: Session) -> list[Recording]:
    """Return all recordings ordered by upload date descending."""
    return session.query(Recording).order_by(Recording.uploaded_at.desc()).all()


# ── Segments ──────────────────────────────────────────────────────────────────


def get_segment(session: Session, segment_id: str) -> Segment | None:
    """Return a Segment by exact ID or unique prefix, or None."""
    seg = session.get(Segment, segment_id)
    if seg:
        return seg
    matches = session.query(Segment).filter(Segment.id.like(f"{segment_id}%")).all()
    return matches[0] if len(matches) == 1 else None


def require_segment(session: Session, segment_id: str) -> Segment:
    """Return a Segment by ID or prefix, raising ValueError if not found."""
    seg = get_segment(session, segment_id)
    if not seg:
        raise ValueError(f"Segment '{segment_id}' not found.")
    return seg


def get_chain(session: Session, recording_id: str) -> list[Segment]:
    """Return ordered active segment chain for a recording (head → tail)."""
    segments = (
        session.query(Segment)
        .filter(Segment.recording_id == recording_id, Segment.status == "active")
        .all()
    )
    if not segments:
        return []

    by_id = {seg.id: seg for seg in segments}
    active_ids = set(by_id)

    # Head = segment whose prev_segment_id is None or points outside active set
    heads = [seg for seg in segments if seg.prev_segment_id not in active_ids]
    if not heads:
        return segments  # cycle guard fallback

    chain: list[Segment] = []
    current: Segment | None = heads[0]
    visited: set[str] = set()
    while current and current.id not in visited:
        chain.append(current)
        visited.add(current.id)
        current = by_id.get(current.next_segment_id or "")

    return chain


# ── Tasks ─────────────────────────────────────────────────────────────────────


def get_task(session: Session, task_id: str) -> Task | None:
    """Return a Task by exact ID or unique prefix, or None."""
    task = session.get(Task, task_id)
    if task:
        return task
    matches = session.query(Task).filter(Task.id.like(f"{task_id}%")).all()
    return matches[0] if len(matches) == 1 else None


def require_task(session: Session, task_id: str) -> Task:
    """Return a Task by ID or prefix, raising ValueError if not found."""
    task = get_task(session, task_id)
    if not task:
        raise ValueError(f"Task '{task_id}' not found.")
    return task


def list_tasks(
    session: Session,
    *,
    task_type: str | None = None,
    status: str | None = None,
    assigned_to_name: str | None = None,
    segment_id: str | None = None,
    recording_id: str | None = None,
) -> list[Task]:
    """Return tasks matching the given filters, ordered by creation date."""
    query = session.query(Task)
    if task_type is not None:
        query = query.filter(Task.type == task_type)
    if status is not None:
        query = query.filter(Task.status == status)
    if assigned_to_name is not None:
        user = get_user(session, assigned_to_name)
        query = query.filter(Task.assigned_to == (user.id if user else "__none__"))
    if segment_id is not None:
        query = query.filter(Task.segment_id == segment_id)
    if recording_id is not None:
        seg_ids = [
            seg.id
            for seg in session.query(Segment.id).filter(
                Segment.recording_id == recording_id
            )
        ]
        query = query.filter(Task.segment_id.in_(seg_ids))
    return query.order_by(Task.created_at).all()


def get_task_saves(session: Session, task_id: str) -> list[TaskSave]:
    """Return all saves for a task ordered by save date."""
    return (
        session.query(TaskSave)
        .filter_by(task_id=task_id)
        .order_by(TaskSave.saved_at)
        .all()
    )


def get_task_chain(session: Session, task_id: str) -> list[Task]:
    """Walk parent_task_id backwards to return the full annotation/validation chain."""
    chain: list[Task] = []
    visited: set[str] = set()
    current = get_task(session, task_id)
    while current and current.id not in visited:
        chain.append(current)
        visited.add(current.id)
        if current.parent_task_id:
            current = get_task(session, current.parent_task_id)
        else:
            break
    chain.reverse()
    return chain
