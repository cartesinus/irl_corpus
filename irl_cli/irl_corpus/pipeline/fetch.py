"""Fetch a task's working files (EAF + media) to a local directory."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from irl_corpus.db.models import Recording
from irl_corpus.db.queries import get_task_saves, require_task
from irl_corpus.storage import Storage


@dataclass
class FetchPlan:
    """Everything needed to fetch a task."""

    task_id: str
    eaf_remote: str
    eaf_source: str  # "save" | "base"
    media_remote: str
    recording_id: str
    media_filename: str
    segment_start_ms: int


@dataclass
class FetchResult:
    """Outcome of a fetch."""

    eaf_path: Path
    media_path: Path
    media_cached: bool


def plan_fetch(session: Session, task_id: str) -> FetchPlan:
    """Resolve which EAF and media file a task needs."""
    task = require_task(session, task_id)
    seg = task.segment
    rec = session.get(Recording, seg.recording_id)
    if rec is None:
        raise ValueError(f"Recording for segment {seg.id} not found.")

    # Latest save wins over the base EAF so partial work is resumed.
    saves = get_task_saves(session, task.id)
    if saves:
        eaf_remote, eaf_source = saves[-1].eaf_path, "save"
    elif task.base_eaf_path:
        eaf_remote, eaf_source = task.base_eaf_path, "base"
    else:
        raise ValueError(
            f"Task {task.id[:8]} has no EAF yet — it has no saves and no base EAF."
        )

    return FetchPlan(
        task_id=task.id,
        eaf_remote=eaf_remote,
        eaf_source=eaf_source,
        media_remote=rec.storage_path,
        recording_id=rec.id,
        media_filename=Path(rec.storage_path).name,
        segment_start_ms=seg.start_ms or 0,
    )


def rewrite_media_url(
    eaf_path: Path,
    media_url: str,
    relative_media_url: str,
    time_origin_ms: int,
) -> None:
    """Point an EAF's media descriptor at a different media file."""
    # Without this the xsi:noNamespaceSchemaLocation attribute is dropped on write.
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    tree = ET.parse(eaf_path)
    root = tree.getroot()

    header = root.find("HEADER")
    if header is None:
        header = ET.SubElement(root, "HEADER", {"TIME_UNITS": "milliseconds"})

    descriptor = header.find("MEDIA_DESCRIPTOR")
    if descriptor is None:
        descriptor = ET.SubElement(header, "MEDIA_DESCRIPTOR", {"MIME_TYPE": "video/mp4"})

    descriptor.set("MEDIA_URL", media_url)
    descriptor.set("RELATIVE_MEDIA_URL", relative_media_url)
    descriptor.set("TIME_ORIGIN", str(time_origin_ms))

    tree.write(eaf_path, encoding="utf-8", xml_declaration=True)


def _download_verified(
    storage: Storage, remote: str, local: Path, *, force: bool = False
) -> bool:
    """Download remote file. Returns True if the existing local copy was reused."""
    expected = storage.size(remote)

    if local.exists() and not force:
        if expected is None or local.stat().st_size == expected:
            return True

    part = local.with_suffix(local.suffix + ".part")
    try:
        storage.get(remote, part)
        if expected is not None and part.stat().st_size != expected:
            raise IOError(
                f"Size mismatch for {remote}: "
                f"got {part.stat().st_size} bytes, expected {expected}."
            )
        part.replace(local)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return False


def fetch_task(
    plan: FetchPlan, storage: Storage, dest_root: Path, *, force: bool = False
) -> FetchResult:
    """Download a task's media and EAF, repointing the EAF at the local media."""
    media_local = dest_root / "media" / plan.recording_id / plan.media_filename
    task_dir = dest_root / "tasks" / plan.task_id
    eaf_local = task_dir / f"{plan.task_id[:8]}.eaf"

    # Media first: a failed transfer then leaves no half-rewritten EAF behind.
    media_cached = _download_verified(
        storage, plan.media_remote, media_local, force=force
    )
    _download_verified(storage, plan.eaf_remote, eaf_local, force=True)

    media_abs = media_local.resolve()
    rewrite_media_url(
        eaf_local,
        media_url=f"file://{media_abs}",
        relative_media_url=os.path.relpath(media_abs, task_dir.resolve()),
        time_origin_ms=0,
    )

    return FetchResult(
        eaf_path=eaf_local, media_path=media_local, media_cached=media_cached
    )
