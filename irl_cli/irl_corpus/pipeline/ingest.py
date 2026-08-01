"""Recording upload and segment splitting."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sqlalchemy import delete as sql_delete, select, update as sql_update
from sqlalchemy.orm import Session

from irl_corpus.db.models import Recording, Segment, SegmentEvent, Task, TaskSave
from irl_corpus.db.queries import get_user, require_recording
from irl_corpus.storage import Storage


def probe_duration_ms(path: Path) -> int | None:
    """Return the duration of a media file in milliseconds using ffprobe.

    Returns None if ffprobe is unavailable or the file cannot be probed.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        duration_secs = float(data.get("format", {}).get("duration", 0))
        return int(duration_secs * 1000)
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        ValueError,
        FileNotFoundError,
    ):
        return None


def transcode_to_mp4(
    src: Path, dst: Path
) -> Path:  # pylint: disable=too-many-arguments
    """Transcode *src* to *dst* as MP4 using ffmpeg stream-copy (no re-encoding).

    Raises subprocess.CalledProcessError if ffmpeg fails.
    """
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-c",
            "copy",
            "-y",
            str(dst),
        ],
        check=True,
    )
    return dst


def upload_recording(
    session: Session,
    storage: Storage,
    local_path: Path,
    title: str,
    user_name: str | None = None,
) -> Recording:
    """Copy a video file into storage and register the Recording in the DB."""
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(f"File not found: {local_path}")

    user_id = None
    if user_name:
        user = get_user(session, user_name)
        if user:
            user_id = user.id

    rec = Recording(
        title=title,
        original_filename=local_path.name,
        storage_path="",  # filled after upload
        status="uploading",
        uploaded_by=user_id,
    )
    session.add(rec)
    session.flush()  # get rec.id

    storage_path = f"recordings/{rec.id}/{local_path.name}"
    storage.put(local_path, storage_path)

    rec.storage_path = storage_path
    rec.status = "ready"
    session.flush()
    return rec


def split_into_segments(
    session: Session,
    recording_id: str,
    user_name: str | None = None,
    segment_duration_ms: int = 1_800_000,  # 30 minutes
) -> list[Segment]:
    """Split a recording into fixed-duration content segments (linked list).

    Segments reference ranges in the original recording; no video extraction here.
    Call pipeline.auto.extract_segment_clips() separately to extract audio/video.
    """
    rec = require_recording(session, recording_id)
    if not rec.duration_ms:
        raise ValueError(
            f"Recording {recording_id} has no duration_ms. "
            "Set it before splitting (e.g. after probing the file)."
        )

    user_id = None
    if user_name:
        user = get_user(session, user_name)
        if user:
            user_id = user.id

    segments: list[Segment] = []
    start = 0
    while start < rec.duration_ms:
        end = min(start + segment_duration_ms, rec.duration_ms)
        seg = Segment(
            recording_id=rec.id,
            type="content",
            start_ms=start,
            end_ms=end,
            status="active",
            created_by=user_id,
            annotation_status="pending",
        )
        session.add(seg)
        session.flush()

        # wire linked list
        if segments:
            prev = segments[-1]
            prev.next_segment_id = seg.id
            seg.prev_segment_id = prev.id

        session.add(
            SegmentEvent(
                segment_id=seg.id,
                user_id=user_id,
                event_type="created",
                payload=json.dumps({"start_ms": start, "end_ms": end}),
            )
        )

        segments.append(seg)
        start = end

    rec.status = "ready"
    session.flush()
    return segments


def delete_recording(
    session: Session,
    storage: Storage,
    recording_id: str,
) -> Recording:
    """Hard-delete a recording and all associated data from DB and storage.

    Deletion order:
      TaskSaves → Tasks → SegmentEvents → Segments (FKs cleared) → Recording
      Storage:  tasks/<task_id>/ → segments/<seg_id>/ → recordings/<rec_id>/
    """
    rec = require_recording(session, recording_id)
    rid = rec.id

    # Collect IDs for storage cleanup before deleting
    seg_ids = [
        row[0]
        for row in session.execute(
            select(Segment.id).where(Segment.recording_id == rid)
        )
    ]
    task_ids = (
        [
            row[0]
            for row in session.execute(
                select(Task.id).where(Task.segment_id.in_(seg_ids))
            )
        ]
        if seg_ids
        else []
    )

    # Delete in dependency order using Core expressions (avoids ORM identity-map conflicts)
    if task_ids:
        session.execute(sql_delete(TaskSave).where(TaskSave.task_id.in_(task_ids)))
        session.execute(sql_delete(Task).where(Task.id.in_(task_ids)))
    if seg_ids:
        session.execute(
            sql_delete(SegmentEvent).where(SegmentEvent.segment_id.in_(seg_ids))
        )
        session.execute(
            sql_update(Segment)
            .where(Segment.recording_id == rid)
            .values(
                prev_segment_id=None, next_segment_id=None, split_from_segment_id=None
            )
        )
        session.execute(sql_delete(Segment).where(Segment.recording_id == rid))
    session.execute(sql_delete(Recording).where(Recording.id == rid))
    session.flush()

    # Delete storage files
    for task_id in task_ids:
        storage.delete_tree(f"tasks/{task_id}")
    for seg_id in seg_ids:
        storage.delete_tree(f"segments/{seg_id}")
    storage.delete_tree(f"recordings/{rec.id}")

    return rec


def set_recording_duration(
    session: Session,
    recording_id: str,
    duration_ms: int,
) -> Recording:
    """Update the duration of a recording (call after probing with ffprobe etc.)."""
    rec = require_recording(session, recording_id)
    rec.duration_ms = duration_ms
    session.flush()
    return rec
