"""Task lifecycle: create, assign, work, save, finish, validate."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from irl_corpus.db.models import AnnotationSchema, Recording, Segment, Task, TaskSave
from irl_corpus.db.queries import (
    get_chain,
    get_current_schema,
    get_schema_by_version,
    list_tasks,
    require_current_schema,
    require_segment,
    require_task,
    require_user,
)
from irl_corpus.pipeline import fetch
from irl_corpus.storage import Storage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _put_eaf(
    session: Session,
    task: Task,
    eaf_file: Path,
    storage: Storage,
    storage_path: str,
) -> None:
    """Store an annotator's EAF, repointing its media back at the corpus copy."""
    seg = require_segment(session, task.segment_id)
    rec = session.get(Recording, seg.recording_id)
    if rec is None:
        storage.put(eaf_file, storage_path)
        return

    abs_media = storage.url(rec.storage_path)
    if not abs_media.startswith(("file://", "s3://")):
        abs_media = f"file://{abs_media}"

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / Path(storage_path).name
        shutil.copyfile(eaf_file, staged)
        fetch.rewrite_media_url(
            staged,
            media_url=abs_media,
            relative_media_url=os.path.relpath(
                rec.storage_path, str(Path(storage_path).parent)
            ),
            time_origin_ms=seg.start_ms or 0,
        )
        storage.put(staged, storage_path)


# ── task creation ─────────────────────────────────────────────────────────────


def create_annotation_task(
    session: Session,
    segment_id: str,
    schema: AnnotationSchema | None = None,
    base_eaf_path: str | None = None,
) -> Task:
    """Create a pending annotation task for a segment."""
    seg = require_segment(session, segment_id)
    if schema is None:
        schema = require_current_schema(session)

    task = Task(
        segment_id=seg.id,
        schema_id=schema.id,
        type="annotation",
        status="pending",
        round=1,
        base_eaf_path=base_eaf_path,
    )
    session.add(task)
    session.flush()
    _sync_segment_annotation_status(session, seg.id)
    return task


def create_tasks_for_recording(
    session: Session,
    recording_id: str,
    schema: AnnotationSchema | None = None,
) -> list[Task]:
    """Create annotation tasks for all active content segments of a recording."""
    if schema is None:
        schema = require_current_schema(session)

    chain = get_chain(session, recording_id)
    tasks = []
    for seg in chain:
        if seg.type == "content" and seg.annotation_status == "pending":
            task = create_annotation_task(session, seg.id, schema=schema)
            tasks.append(task)
    return tasks


# ── assignment / soft lock ────────────────────────────────────────────────────


def assign_task(session: Session, task_id: str, user_name: str) -> Task:
    """Formally assign a task to a user (does not set working_on)."""
    task = require_task(session, task_id)
    user = require_user(session, user_name)
    task.assigned_to = user.id
    task.updated_at = _now()
    session.flush()
    return task


def take_task(session: Session, task_id: str, user_name: str) -> Task:
    """Assign to self and set working_on (soft lock). Moves status to in_progress."""
    task = require_task(session, task_id)
    user = require_user(session, user_name)
    task.assigned_to = user.id
    task.working_on = user.id
    if task.status == "pending":
        task.status = "in_progress"
    task.updated_at = _now()
    session.flush()
    _sync_segment_annotation_status(session, task.segment_id)
    return task


def set_working(session: Session, task_id: str, user_name: str) -> Task:
    """Set working_on without changing assignment (already assigned, just opening it)."""
    task = require_task(session, task_id)
    user = require_user(session, user_name)
    task.working_on = user.id
    if task.status == "pending":
        task.status = "in_progress"
    task.updated_at = _now()
    session.flush()
    return task


def release_working(session: Session, task_id: str) -> Task:
    """Clear the working_on soft lock (closed ELAN, stepping away)."""
    task = require_task(session, task_id)
    task.working_on = None
    task.updated_at = _now()
    session.flush()
    return task


# ── saving work ───────────────────────────────────────────────────────────────


def save_partial(  # pylint: disable=too-many-arguments
    session: Session,
    task_id: str,
    eaf_file: Path,
    user_name: str,
    storage: Storage,
    notes: str | None = None,
) -> TaskSave:
    """Save an intermediate EAF snapshot without finishing the task."""
    task = require_task(session, task_id)
    user = require_user(session, user_name)

    save_num = (
        session.query(TaskSave).filter_by(task_id=task.id, is_final=False).count() + 1
    )
    storage_path = f"tasks/{task.id}/saves/{save_num:03d}_{_ts()}_{user.name}.eaf"
    _put_eaf(session, task, eaf_file, storage, storage_path)

    task_save = TaskSave(
        task_id=task.id,
        saved_by=user.id,
        eaf_path=storage_path,
        is_final=False,
        notes=notes,
    )
    session.add(task_save)
    task.updated_at = _now()
    session.flush()
    return task_save


# ── finishing annotation ──────────────────────────────────────────────────────


def finish_annotation(  # pylint: disable=too-many-arguments
    session: Session,
    task_id: str,
    eaf_file: Path,
    user_name: str,
    storage: Storage,
    notes: str | None = None,
) -> tuple[Task, Task]:
    """Mark annotation done, store final EAF, auto-create validation task.

    Returns (annotation_task, new_validation_task).
    """
    task = require_task(session, task_id)
    user = require_user(session, user_name)

    if task.type != "annotation":
        raise ValueError(
            f"Task {task.id} is type='{task.type}', expected 'annotation'."
        )
    if task.status not in ("pending", "in_progress"):
        raise ValueError(f"Task {task.id} has status='{task.status}', cannot finish.")

    storage_path = f"tasks/{task.id}/saves/final_{_ts()}_{user.name}.eaf"
    _put_eaf(session, task, eaf_file, storage, storage_path)

    session.add(
        TaskSave(
            task_id=task.id,
            saved_by=user.id,
            eaf_path=storage_path,
            is_final=True,
            notes=notes,
        )
    )

    task.status = "done"
    task.completed_at = _now()
    task.updated_at = _now()
    task.working_on = None

    # Auto-create validation task
    val_task = Task(
        segment_id=task.segment_id,
        schema_id=task.schema_id,
        type="validation",
        status="pending",
        parent_task_id=task.id,
        round=task.round,
        base_eaf_path=storage_path,
    )
    session.add(val_task)
    session.flush()
    _sync_segment_annotation_status(session, task.segment_id)
    return task, val_task


# ── validation ────────────────────────────────────────────────────────────────


def approve_validation(
    session: Session,
    task_id: str,
    user_name: str,
) -> Task:
    """Approve a validation task. Segment becomes 'validated'."""
    task = require_task(session, task_id)
    user = require_user(session, user_name)

    if task.type != "validation":
        raise ValueError(
            f"Task {task.id} is type='{task.type}', expected 'validation'."
        )
    if task.status not in ("pending", "in_progress"):
        raise ValueError(f"Task {task.id} has status='{task.status}', cannot approve.")

    task.status = "approved"
    task.assigned_to = user.id
    task.working_on = None
    task.completed_at = _now()
    task.updated_at = _now()

    session.flush()
    _sync_segment_annotation_status(session, task.segment_id)
    return task


def reject_validation(  # pylint: disable=too-many-arguments
    session: Session,
    task_id: str,
    eaf_file: Path,
    user_name: str,
    storage: Storage,
    notes: str | None = None,
) -> tuple[Task, Task]:
    """Reject validation, saving the validator's annotated EAF.

    The validator's EAF (with notes tier filled in) becomes the base for the
    next annotation round.

    Returns (rejected_validation_task, new_annotation_task).
    """
    task = require_task(session, task_id)
    user = require_user(session, user_name)

    if task.type != "validation":
        raise ValueError(
            f"Task {task.id} is type='{task.type}', expected 'validation'."
        )
    if task.status not in ("pending", "in_progress"):
        raise ValueError(f"Task {task.id} has status='{task.status}', cannot reject.")

    storage_path = f"tasks/{task.id}/saves/rejected_{_ts()}_{user.name}.eaf"
    _put_eaf(session, task, eaf_file, storage, storage_path)

    session.add(
        TaskSave(
            task_id=task.id,
            saved_by=user.id,
            eaf_path=storage_path,
            is_final=True,
            notes=notes,
        )
    )

    task.status = "rejected"
    task.assigned_to = user.id
    task.working_on = None
    task.completed_at = _now()
    task.updated_at = _now()

    # New annotation task for round+1; base = validator's annotated EAF
    new_task = Task(
        segment_id=task.segment_id,
        schema_id=task.schema_id,
        type="annotation",
        status="pending",
        parent_task_id=task.id,
        round=task.round + 1,
        base_eaf_path=storage_path,
    )
    session.add(new_task)
    session.flush()
    _sync_segment_annotation_status(session, task.segment_id)
    return task, new_task


# ── cancellation ─────────────────────────────────────────────────────────────


def cancel_task(session: Session, task_id: str) -> Task:
    """Cancel a task, clearing assignment and working lock."""
    task = require_task(session, task_id)
    task.status = "cancelled"
    task.working_on = None
    task.completed_at = _now()
    task.updated_at = _now()
    session.flush()
    _sync_segment_annotation_status(session, task.segment_id)
    return task


# ── reannotation (schema bump) ────────────────────────────────────────────────


def trigger_reannotation(
    session: Session,
    segment_id: str,
    schema_version: str | None = None,
) -> Task:
    """Supersede existing tasks and create a fresh annotation task for a new schema.

    If schema_version is None the current schema is used.
    """
    seg = require_segment(session, segment_id)

    if schema_version:
        schema = get_schema_by_version(session, schema_version)
        if not schema:
            raise ValueError(f"Schema version '{schema_version}' not found.")
    else:
        schema = require_current_schema(session)

    # Mark current active tasks as superseded
    active_tasks = [
        active_task
        for active_task in list_tasks(session, segment_id=segment_id)
        if active_task.status not in ("cancelled",)
        and active_task.superseded_by is None
    ]

    new_task = Task(
        segment_id=seg.id,
        schema_id=schema.id,
        type="annotation",
        status="pending",
        round=1,
    )
    session.add(new_task)
    session.flush()

    for active_task in active_tasks:
        active_task.superseded_by = new_task.id

    seg.annotation_status = "pending"
    session.flush()
    return new_task


# ── status sync ───────────────────────────────────────────────────────────────


def _sync_segment_annotation_status(session: Session, segment_id: str) -> str:
    """Recompute and persist Segment.annotation_status from its tasks."""
    tasks = list_tasks(session, segment_id=segment_id)
    # Only consider non-superseded tasks
    active = [task for task in tasks if task.superseded_by is None]

    current_schema = get_current_schema(session)

    # validated: approved validation for current schema
    if current_schema:
        if any(
            task.type == "validation"
            and task.status == "approved"
            and task.schema_id == current_schema.id
            for task in active
        ):
            _set_annotation_status(session, segment_id, "validated")
            return "validated"

    # in_validation: a validation task exists and is pending/in_progress
    if any(
        task.type == "validation" and task.status in ("pending", "in_progress")
        for task in active
    ):
        _set_annotation_status(session, segment_id, "in_validation")
        return "in_validation"

    # needs_rework: rejected validation + new annotation task round>1
    if any(
        task.type == "validation" and task.status == "rejected" for task in active
    ) and any(
        task.type == "annotation"
        and task.status in ("pending", "in_progress")
        and task.round > 1
        for task in active
    ):
        _set_annotation_status(session, segment_id, "needs_rework")
        return "needs_rework"

    # in_annotation: annotation task pending/in_progress
    if any(
        task.type == "annotation" and task.status in ("pending", "in_progress")
        for task in active
    ):
        _set_annotation_status(session, segment_id, "in_annotation")
        return "in_annotation"

    _set_annotation_status(session, segment_id, "pending")
    return "pending"


def _set_annotation_status(session: Session, segment_id: str, status: str) -> None:
    seg = session.get(Segment, segment_id)
    if seg:
        seg.annotation_status = status
