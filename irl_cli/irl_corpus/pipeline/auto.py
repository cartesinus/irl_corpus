"""Auto-annotation: create base EAF files and run annotation scripts.

The base EAF is an ELAN annotation file pre-populated with:
- A media descriptor pointing to the segment's audio/video
- One empty TIER for every tier defined in the annotation schema
- TIME_ORIGIN set to segment.start_ms so times align with the original recording

Automatic model-based annotation (ASR, scene classification, etc.) is called
from here and writes into the appropriate tiers before the base EAF is stored.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from irl_corpus.db.models import AnnotationSchema, Segment
from irl_corpus.db.queries import require_segment
from irl_corpus.pipeline.schema import get_tier_names
from irl_corpus.storage import Storage


# ── EAF builder ───────────────────────────────────────────────────────────────


def build_base_eaf(
    segment: Segment,
    schema: AnnotationSchema,
    media_path: str,
    *,
    media_relative_url: str | None = None,
    media_mime: str = "video/mp4",
) -> ET.ElementTree:
    """Return an ElementTree for an empty EAF file with all schema tiers.

    Times in the EAF are absolute (matching the original recording).
    ELAN's TIME_ORIGIN is set to segment.start_ms so the player starts at the
    right position.

    Args:
        segment: The Segment ORM object.
        schema: The AnnotationSchema to draw tier names from.
        media_path: Absolute local path or S3 URL of the recording file.
        media_relative_url: Path to the recording relative to the EAF file
            (used as RELATIVE_MEDIA_URL so ELAN can find the file when moved).
        media_mime: MIME type of the media file.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    root = ET.Element(
        "ANNOTATION_DOCUMENT",
        {
            "AUTHOR": "irl_corpus",
            "DATE": now_iso,
            "FORMAT": "3.0",
            "VERSION": "3.0",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": (
                "http://www.mpi.nl/tools/elan/EAFv3.0.xsd"
            ),
        },
    )

    header = ET.SubElement(
        root,
        "HEADER",
        {
            "MEDIA_FILE": "",
            "TIME_UNITS": "milliseconds",
        },
    )

    media_url = (
        media_path
        if media_path.startswith(("file://", "s3://"))
        else f"file://{media_path}"
    )
    media_descriptor_attrs = {
        "MEDIA_URL": media_url,
        "MIME_TYPE": media_mime,
        "RELATIVE_MEDIA_URL": media_relative_url or media_url,
        "TIME_ORIGIN": str(segment.start_ms or 0),
    }
    ET.SubElement(header, "MEDIA_DESCRIPTOR", media_descriptor_attrs)

    ET.SubElement(header, "PROPERTY", {"NAME": "URN"}).text = (
        f"urn:irl-corpus:segment:{segment.id}"
    )
    ET.SubElement(header, "PROPERTY", {"NAME": "lastUsedAnnotationId"}).text = "0"
    ET.SubElement(header, "PROPERTY", {"NAME": "irl_corpus_segment_id"}).text = (
        segment.id
    )
    ET.SubElement(header, "PROPERTY", {"NAME": "irl_corpus_schema_version"}).text = (
        json.loads(schema.content).get("version", "")
    )

    ET.SubElement(root, "TIME_ORDER")

    # One empty tier per schema tier
    for tier_id in get_tier_names(schema):
        ET.SubElement(
            root,
            "TIER",
            {
                "LINGUISTIC_TYPE_REF": "default-lt",
                "TIER_ID": tier_id,
            },
        )

    ET.SubElement(
        root,
        "LINGUISTIC_TYPE",
        {
            "GRAPHIC_REFERENCES": "false",
            "LINGUISTIC_TYPE_ID": "default-lt",
            "TIME_ALIGNABLE": "true",
        },
    )

    _indent_xml(root)
    return ET.ElementTree(root)


def write_base_eaf(
    session: Session,
    segment_id: str,
    schema: AnnotationSchema,
    storage: Storage,
    recording_storage_path: str,
    *,
    media_mime: str = "video/mp4",
) -> str:
    """Build and store a base EAF for a segment. Returns the storage path.

    The EAF's MEDIA_URL points to the recording's absolute path, and
    RELATIVE_MEDIA_URL is computed relative to the EAF's storage location so
    ELAN can resolve the media when the corpus is moved.
    """
    seg = require_segment(session, segment_id)
    schema_version = json.loads(schema.content).get("version", "unknown")
    eaf_storage_path = f"segments/{seg.id}/base_{schema_version}.eaf"

    # Absolute path for MEDIA_URL
    abs_media = storage.url(recording_storage_path)

    # Relative path from the EAF's directory to the recording
    eaf_dir = str(Path(eaf_storage_path).parent)
    relative_media = os.path.relpath(recording_storage_path, eaf_dir)

    tree = build_base_eaf(
        seg,
        schema,
        abs_media,
        media_relative_url=relative_media,
        media_mime=media_mime,
    )

    with tempfile.NamedTemporaryFile(suffix=".eaf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        tree.write(tmp_path, encoding="UTF-8", xml_declaration=True)
        storage.put(tmp_path, eaf_storage_path)
    finally:
        os.unlink(tmp_path)

    return eaf_storage_path


# ── media extraction helpers ──────────────────────────────────────────────────


def extract_segment_clip(
    segment: Segment,
    recording_local_path: Path,
    out_path: Path,
) -> Path:
    """Extract the segment's time range from the recording as an MP4 (stream-copy)."""
    start_sec = (segment.start_ms or 0) / 1000
    end_sec = (segment.end_ms or 0) / 1000
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start_sec),
            "-to",
            str(end_sec),
            "-i",
            str(recording_local_path),
            "-c",
            "copy",
            str(out_path),
        ],
        check=True,
    )
    return out_path


def extract_segment_wav(
    segment: Segment,
    recording_local_path: Path,
    out_path: Path,
    sample_rate: int = 16000,
) -> Path:
    """Extract the segment's audio from the recording as a 16 kHz mono WAV."""
    start_sec = (segment.start_ms or 0) / 1000
    end_sec = (segment.end_ms or 0) / 1000
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(start_sec),
            "-to",
            str(end_sec),
            "-i",
            str(recording_local_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            str(out_path),
        ],
        check=True,
    )
    return out_path


# ── helpers ───────────────────────────────────────────────────────────────────


def _indent_xml(elem: ET.Element, level: int = 0) -> None:
    indent = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        last_child = None
        for child in elem:
            _indent_xml(child, level + 1)
            last_child = child
        if last_child is not None and (
            not last_child.tail or not last_child.tail.strip()
        ):
            last_child.tail = indent
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = indent
