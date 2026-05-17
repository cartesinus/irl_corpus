#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
Add ASR transcripts and translations from JSON to an ELAN EAF file.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--eaf", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--transcript-tier", default="transcript")
    parser.add_argument("--translation-tier", default="translation")
    parser.add_argument("--clear-existing", action="store_true")
    return parser.parse_args()


def indent_xml(elem: ET.Element, level: int = 0) -> None:
    """Pretty-print XML tree in place."""
    indent = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        children = list(elem)
        for child in children:
            indent_xml(child, level + 1)
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = indent


def next_numeric_id(root: ET.Element, prefix: str, attr_name: str) -> int:
    """Return next integer suffix for XML attributes like a12 or ts45."""
    max_id = 0
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")

    for elem in root.iter():
        value = elem.attrib.get(attr_name)
        if not value:
            continue
        match = pattern.match(value)
        if match:
            max_id = max(max_id, int(match.group(1)))

    return max_id + 1


def ensure_time_order(root: ET.Element) -> ET.Element:
    """Return existing TIME_ORDER element or create one."""
    time_order = root.find("TIME_ORDER")
    if time_order is not None:
        return time_order

    header = root.find("HEADER")
    time_order = ET.Element("TIME_ORDER")

    if header is None:
        root.insert(0, time_order)
    else:
        children = list(root)
        root.insert(children.index(header) + 1, time_order)

    return time_order


def ensure_linguistic_type(root: ET.Element, type_id: str = "lt_free_text") -> str:
    """Ensure a free-text linguistic type exists and return its ID."""
    for elem in root.findall("LINGUISTIC_TYPE"):
        if elem.attrib.get("LINGUISTIC_TYPE_ID") == type_id:
            return type_id

    ET.SubElement(
        root,
        "LINGUISTIC_TYPE",
        {
            "LINGUISTIC_TYPE_ID": type_id,
            "TIME_ALIGNABLE": "true",
            "GRAPHIC_REFERENCES": "false",
        },
    )
    return type_id


def ensure_tier(root: ET.Element, tier_id: str, linguistic_type_ref: str) -> ET.Element:
    """Return existing tier or create a new independent tier."""
    for tier in root.findall("TIER"):
        if tier.attrib.get("TIER_ID") == tier_id:
            return tier

    return ET.SubElement(
        root,
        "TIER",
        {
            "TIER_ID": tier_id,
            "LINGUISTIC_TYPE_REF": linguistic_type_ref,
        },
    )


def clear_tier(tier: ET.Element) -> None:
    """Remove all annotations from a tier."""
    for child in list(tier):
        if child.tag == "ANNOTATION":
            tier.remove(child)


def add_segment_annotation(
    tier: ET.Element,
    text: str,
    annotation_id: str,
    start_slot_id: str,
    end_slot_id: str,
) -> None:
    """Add a single alignable annotation to a tier."""
    annotation = ET.SubElement(tier, "ANNOTATION")
    alignable = ET.SubElement(
        annotation,
        "ALIGNABLE_ANNOTATION",
        {
            "ANNOTATION_ID": annotation_id,
            "TIME_SLOT_REF1": start_slot_id,
            "TIME_SLOT_REF2": end_slot_id,
        },
    )
    value = ET.SubElement(alignable, "ANNOTATION_VALUE")
    value.text = text


def add_time_slot(time_order: ET.Element, slot_id: str, time_ms: int) -> None:
    """Add a TIME_SLOT entry."""
    ET.SubElement(
        time_order,
        "TIME_SLOT",
        {
            "TIME_SLOT_ID": slot_id,
            "TIME_VALUE": str(time_ms),
        },
    )


def load_segments(json_path: Path) -> list[dict[str, Any]]:
    """Load ASR segments from merged transcription JSON."""
    with json_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    segments = data.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("JSON field 'segments' must be a list.")

    return segments


def main() -> None:
    """Run EAF import."""
    args = parse_args()

    tree = ET.parse(args.eaf)
    root = tree.getroot()

    segments = load_segments(args.json)

    time_order = ensure_time_order(root)
    linguistic_type_ref = ensure_linguistic_type(root)

    transcript_tier = ensure_tier(root, args.transcript_tier, linguistic_type_ref)
    translation_tier = ensure_tier(root, args.translation_tier, linguistic_type_ref)

    if args.clear_existing:
        clear_tier(transcript_tier)
        clear_tier(translation_tier)

    next_time_slot_id = next_numeric_id(root, "ts", "TIME_SLOT_ID")
    next_annotation_id = next_numeric_id(root, "a", "ANNOTATION_ID")

    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])

        start_ms = int(round(start * 1000))
        end_ms = int(round(end * 1000))

        start_slot_id = f"ts{next_time_slot_id}"
        end_slot_id = f"ts{next_time_slot_id + 1}"
        next_time_slot_id += 2

        add_time_slot(time_order, start_slot_id, start_ms)
        add_time_slot(time_order, end_slot_id, end_ms)

        text = str(segment.get("text", "")).strip()
        translated_text = str(segment.get("translated_text", "")).strip()

        if text:
            add_segment_annotation(
                transcript_tier,
                text,
                f"a{next_annotation_id}",
                start_slot_id,
                end_slot_id,
            )
            next_annotation_id += 1

        if translated_text:
            add_segment_annotation(
                translation_tier,
                translated_text,
                f"a{next_annotation_id}",
                start_slot_id,
                end_slot_id,
            )
            next_annotation_id += 1

    indent_xml(root)
    tree.write(args.out, encoding="UTF-8", xml_declaration=True)

    print(f"Wrote: {args.out}")
    print(f"Added {len(segments)} transcript/translation intervals.")


if __name__ == "__main__":
    main()
