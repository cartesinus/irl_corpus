"""Segment sequence operations: split, remove, insert gap."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from irl_corpus.db.models import Segment, SegmentEvent
from irl_corpus.db.queries import get_user, require_segment


def _now() -> datetime:
    return datetime.now(timezone.utc)


def split_segment(
    session: Session,
    segment_id: str,
    at_ms: int,
    user_name: str | None = None,
) -> tuple[Segment, Segment]:
    """Split a content segment into two at ``at_ms`` (offset in the original recording).

    Returns (left_segment, right_segment).
    The original segment is marked as removed; both children reference it via
    split_from_segment_id.
    """
    seg = require_segment(session, segment_id)
    if seg.type != "content":
        raise ValueError(f"Only content segments can be split (got type='{seg.type}').")
    if seg.status == "removed":
        raise ValueError(f"Segment {segment_id} is already removed.")
    if seg.start_ms is None or seg.end_ms is None:
        raise ValueError(f"Segment {segment_id} has no start/end times.")
    if not seg.start_ms < at_ms < seg.end_ms:
        raise ValueError(
            f"at_ms={at_ms} must be between {seg.start_ms} and {seg.end_ms}."
        )

    user_id = _resolve_user_id(session, user_name)

    # Create left child
    left = Segment(
        recording_id=seg.recording_id,
        type="content",
        start_ms=seg.start_ms,
        end_ms=at_ms,
        status="active",
        prev_segment_id=seg.prev_segment_id,
        split_from_segment_id=seg.id,
        created_by=user_id,
        annotation_status="pending",
    )
    # Create right child
    right = Segment(
        recording_id=seg.recording_id,
        type="content",
        start_ms=at_ms,
        end_ms=seg.end_ms,
        status="active",
        next_segment_id=seg.next_segment_id,
        split_from_segment_id=seg.id,
        created_by=user_id,
        annotation_status="pending",
    )

    session.add(left)
    session.add(right)
    session.flush()

    # Wire the chain
    left.next_segment_id = right.id
    right.prev_segment_id = left.id

    # Update neighbours
    if seg.prev_segment_id:
        prev_seg = session.get(Segment, seg.prev_segment_id)
        if prev_seg:
            prev_seg.next_segment_id = left.id

    if seg.next_segment_id:
        next_seg = session.get(Segment, seg.next_segment_id)
        if next_seg:
            next_seg.prev_segment_id = right.id

    # Mark original as removed
    seg.status = "removed"
    seg.version += 1

    session.add(
        SegmentEvent(
            segment_id=seg.id,
            user_id=user_id,
            event_type="split",
            payload=json.dumps(
                {
                    "at_ms": at_ms,
                    "left_id": left.id,
                    "right_id": right.id,
                }
            ),
        )
    )

    session.flush()
    return left, right


def split_segment_with_gap(  # pylint: disable=too-many-arguments
    session: Session,
    segment_id: str,
    gap_start_ms: int,
    gap_end_ms: int,
    user_name: str | None = None,
    gap_notes: str | None = None,
) -> tuple[Segment, Segment, Segment]:
    """Split a segment, inserting an explicit gap between the two halves.

    Returns (left_segment, gap_segment, right_segment).
    """
    seg = require_segment(session, segment_id)
    if seg.type != "content":
        raise ValueError(f"Only content segments can be split (got type='{seg.type}').")
    if seg.status == "removed":
        raise ValueError(f"Segment {segment_id} is already removed.")
    if seg.start_ms is None or seg.end_ms is None:
        raise ValueError(f"Segment {segment_id} has no start/end times.")
    if not seg.start_ms <= gap_start_ms < gap_end_ms <= seg.end_ms:
        raise ValueError(
            f"Gap [{gap_start_ms}, {gap_end_ms}] must be within "
            f"[{seg.start_ms}, {seg.end_ms}]."
        )

    user_id = _resolve_user_id(session, user_name)

    left = Segment(
        recording_id=seg.recording_id,
        type="content",
        start_ms=seg.start_ms,
        end_ms=gap_start_ms,
        status="active",
        prev_segment_id=seg.prev_segment_id,
        split_from_segment_id=seg.id,
        created_by=user_id,
        annotation_status="pending",
    )
    gap = Segment(
        recording_id=seg.recording_id,
        type="gap",
        start_ms=gap_start_ms,
        end_ms=gap_end_ms,
        status="active",
        split_from_segment_id=seg.id,
        notes=gap_notes,
        created_by=user_id,
        annotation_status="pending",
    )
    right = Segment(
        recording_id=seg.recording_id,
        type="content",
        start_ms=gap_end_ms,
        end_ms=seg.end_ms,
        status="active",
        next_segment_id=seg.next_segment_id,
        split_from_segment_id=seg.id,
        created_by=user_id,
        annotation_status="pending",
    )

    session.add(left)
    session.add(gap)
    session.add(right)
    session.flush()

    left.next_segment_id = gap.id
    gap.prev_segment_id = left.id
    gap.next_segment_id = right.id
    right.prev_segment_id = gap.id

    if seg.prev_segment_id:
        prev_seg = session.get(Segment, seg.prev_segment_id)
        if prev_seg:
            prev_seg.next_segment_id = left.id

    if seg.next_segment_id:
        next_seg = session.get(Segment, seg.next_segment_id)
        if next_seg:
            next_seg.prev_segment_id = right.id

    seg.status = "removed"
    seg.version += 1

    session.add(
        SegmentEvent(
            segment_id=seg.id,
            user_id=user_id,
            event_type="split",
            payload=json.dumps(
                {
                    "gap_start_ms": gap_start_ms,
                    "gap_end_ms": gap_end_ms,
                    "left_id": left.id,
                    "gap_id": gap.id,
                    "right_id": right.id,
                }
            ),
        )
    )

    session.flush()
    return left, gap, right


def remove_segment(
    session: Session,
    segment_id: str,
    user_name: str | None = None,
    reason: str | None = None,
    replace_with_gap: bool = True,
) -> Segment:
    """Mark a segment as removed.

    If ``replace_with_gap=True`` (default), a gap placeholder is inserted in
    the chain to preserve the sequence structure and allow gap annotation.
    """
    seg = require_segment(session, segment_id)
    if seg.status == "removed":
        raise ValueError(f"Segment {segment_id} is already removed.")

    user_id = _resolve_user_id(session, user_name)

    if replace_with_gap:
        gap = Segment(
            recording_id=seg.recording_id,
            type="gap",
            start_ms=seg.start_ms,
            end_ms=seg.end_ms,
            status="active",
            prev_segment_id=seg.prev_segment_id,
            next_segment_id=seg.next_segment_id,
            notes=reason,
            created_by=user_id,
            annotation_status="pending",
        )
        session.add(gap)
        session.flush()

        if seg.prev_segment_id:
            prev_seg = session.get(Segment, seg.prev_segment_id)
            if prev_seg:
                prev_seg.next_segment_id = gap.id

        if seg.next_segment_id:
            next_seg = session.get(Segment, seg.next_segment_id)
            if next_seg:
                next_seg.prev_segment_id = gap.id
    else:
        # Stitch neighbours together
        if seg.prev_segment_id:
            prev_seg = session.get(Segment, seg.prev_segment_id)
            if prev_seg:
                prev_seg.next_segment_id = seg.next_segment_id

        if seg.next_segment_id:
            next_seg = session.get(Segment, seg.next_segment_id)
            if next_seg:
                next_seg.prev_segment_id = seg.prev_segment_id

    seg.status = "removed"
    seg.notes = (seg.notes or "") + (
        f" [removed: {reason}]" if reason else " [removed]"
    )
    seg.version += 1

    session.add(
        SegmentEvent(
            segment_id=seg.id,
            user_id=user_id,
            event_type="removed",
            payload=json.dumps(
                {"reason": reason, "replace_with_gap": replace_with_gap}
            ),
        )
    )

    session.flush()
    return seg


# ── helpers ───────────────────────────────────────────────────────────────────


def _resolve_user_id(session: Session, user_name: str | None) -> str | None:
    if not user_name:
        return None
    user = get_user(session, user_name)
    return user.id if user else None
