"""
Add visual scene annotations from a JSON file into an EAF file.

The JSON should be produced by annotate_visual.py. It modifies the tiers:
  acoustic_scene, visual_context, speaker_visibility, topic

If any of those tiers already have annotations, the user is asked whether to:
  [r] Replace them
  [a] Keep them and add new ones
  [s] Skip / do nothing

Usage:
    python scripts/add_visual_annotations_to_eaf.py annotations.json data/clip.eaf
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET

VISUAL_TIERS = ["acoustic_scene", "visual_context", "speaker_visibility", "topic"]


def parse_time_to_ms(t: str) -> int:
    """Convert MM:SS or HH:MM:SS string to milliseconds."""
    parts = t.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60_000 + round(float(seconds) * 1000)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return (
            int(hours) * 3_600_000
            + int(minutes) * 60_000
            + round(float(seconds) * 1000)
        )
    raise ValueError(f"Unrecognised time format: {t!r}")


def get_max_ts_num(root: ET.Element) -> int:
    """Return the highest numeric suffix among all TIME_SLOT_ID attributes."""
    max_n = 0
    for ts in root.iter("TIME_SLOT"):
        n = int(ts.get("TIME_SLOT_ID", "ts0")[2:])
        max_n = max(max_n, n)
    return max_n


def get_max_ann_num(root: ET.Element) -> int:
    """Return the highest numeric suffix among all ANNOTATION_ID attributes."""
    max_n = 0
    for ann in root.iter("ALIGNABLE_ANNOTATION"):
        n = int(ann.get("ANNOTATION_ID", "a0")[1:])
        max_n = max(max_n, n)
    return max_n


def get_last_used_ann_id(root: ET.Element) -> int:
    """Return the lastUsedAnnotationId property value, or 0 if absent."""
    for prop in root.iter("PROPERTY"):
        if prop.get("NAME") == "lastUsedAnnotationId":
            return int(prop.text or "0")
    return 0


def set_last_used_ann_id(root: ET.Element, value: int):
    """Set or create the lastUsedAnnotationId property in the EAF header."""
    for prop in root.iter("PROPERTY"):
        if prop.get("NAME") == "lastUsedAnnotationId":
            prop.text = str(value)
            return
    header = root.find("HEADER")
    if header is not None:
        prop = ET.SubElement(header, "PROPERTY")
        prop.set("NAME", "lastUsedAnnotationId")
        prop.text = str(value)


def tier_annotation_count(root: ET.Element, tier_id: str) -> int:
    """Return the number of alignable annotations in the named tier."""
    for tier in root.iter("TIER"):
        if tier.get("TIER_ID") == tier_id:
            return len(list(tier.iter("ALIGNABLE_ANNOTATION")))
    return 0


def clear_tier(root: ET.Element, tier_id: str):
    """Remove all annotations from the named tier."""
    for tier in root.iter("TIER"):
        if tier.get("TIER_ID") == tier_id:
            for ann in list(tier.findall("ANNOTATION")):
                tier.remove(ann)
            return


def get_or_create_tier(root: ET.Element, tier_id: str) -> ET.Element:
    """Return an existing tier by ID, or create and insert a new one."""
    for tier in root.iter("TIER"):
        if tier.get("TIER_ID") == tier_id:
            return tier
    tier = ET.Element("TIER")
    tier.set("LINGUISTIC_TYPE_REF", "default-lt")
    tier.set("TIER_ID", tier_id)
    # Insert before the first LINGUISTIC_TYPE element
    for i, child in enumerate(root):
        if child.tag == "LINGUISTIC_TYPE":
            root.insert(i, tier)
            return tier
    root.append(tier)
    return tier


def find_or_add_time_slot(time_order: ET.Element, value_ms: int, next_ts: list) -> str:
    """Return existing TIME_SLOT_ID for value_ms, or create a new one."""
    for ts in time_order:
        if int(ts.get("TIME_VALUE", -1)) == value_ms:
            return ts.get("TIME_SLOT_ID")
    ts_id = f"ts{next_ts[0]}"
    next_ts[0] += 1
    ts = ET.Element("TIME_SLOT")
    ts.set("TIME_SLOT_ID", ts_id)
    ts.set("TIME_VALUE", str(value_ms))
    time_order.append(ts)
    return ts_id


def sort_time_order(root: ET.Element):
    """Sort TIME_SLOT elements by TIME_VALUE in ascending order."""
    time_order = root.find("TIME_ORDER")
    if time_order is None:
        return
    slots = list(time_order)
    slots.sort(key=lambda ts: int(ts.get("TIME_VALUE", 0)))
    for ts in slots:
        time_order.remove(ts)
    for ts in slots:
        time_order.append(ts)


def add_tier_annotations(
    root: ET.Element,
    tier_id: str,
    segments: list,
    next_ts: list,
    next_ann: list,
):
    """Add alignable annotations for one tier from the given segments list."""
    time_order = root.find("TIME_ORDER")
    tier = get_or_create_tier(root, tier_id)

    added = 0
    for seg in segments:
        label = seg.get(tier_id)
        if not label:
            continue
        begin_ms = parse_time_to_ms(seg["begin_time"])
        end_ms = parse_time_to_ms(seg["end_time"])

        ts1 = find_or_add_time_slot(time_order, begin_ms, next_ts)
        ts2 = find_or_add_time_slot(time_order, end_ms, next_ts)

        ann_container = ET.SubElement(tier, "ANNOTATION")
        ann = ET.SubElement(ann_container, "ALIGNABLE_ANNOTATION")
        ann.set("ANNOTATION_ID", f"a{next_ann[0]}")
        ann.set("TIME_SLOT_REF1", ts1)
        ann.set("TIME_SLOT_REF2", ts2)
        val = ET.SubElement(ann, "ANNOTATION_VALUE")
        val.text = label

        next_ann[0] += 1
        added += 1

    return added


def indent_xml(elem: ET.Element, level: int = 0):
    """Add pretty-print indentation in-place (works on Python < 3.9 too)."""
    indent = "\n" + "    " * level
    child_indent = "\n" + "    " * (level + 1)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = child_indent
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        children = list(elem)
        for child in children:
            indent_xml(child, level + 1)
        # last child tail should match parent indent
        if not children[-1].tail or not children[-1].tail.strip():
            children[-1].tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def main():
    """Parse arguments, load JSON annotations, and write them into the EAF file."""
    parser = argparse.ArgumentParser(
        description="Add visual annotations from JSON into an EAF file"
    )
    parser.add_argument(
        "annotations_json", help="Path to JSON produced by annotate_visual.py"
    )
    parser.add_argument("eaf_file", help="Path to the .eaf file to modify")
    args = parser.parse_args()

    with open(args.annotations_json, encoding="utf-8") as file:
        data = json.load(file)

    segments = data.get("annotations", [])
    if not segments:
        print("No annotations found in JSON.", file=sys.stderr)
        sys.exit(1)

    # Only process tiers that actually appear in the data
    tiers_present = [t for t in VISUAL_TIERS if any(t in seg for seg in segments)]
    if not tiers_present:
        print("JSON contains none of the expected visual tiers.", file=sys.stderr)
        sys.exit(1)

    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    tree = ET.parse(args.eaf_file)
    root = tree.getroot()

    # Check for existing annotations
    tiers_with_data = [t for t in tiers_present if tier_annotation_count(root, t) > 0]

    action = None
    if tiers_with_data:
        print(f"\nThese tiers already have annotations: {', '.join(tiers_with_data)}")
        print("\nWhat do you want to do?")
        print("  [r] Replace existing annotations with new ones")
        print("  [a] Keep existing and add new annotations alongside")
        print("  [s] Skip — leave the EAF file unchanged")
        while True:
            choice = input("\nEnter choice [r/a/s]: ").strip().lower()
            if choice in ("r", "a", "s"):
                action = choice
                break
            print("Please enter r, a, or s.")

        if action == "s":
            print("Skipping. EAF file unchanged.")
            return

        if action == "r":
            for tier_id in tiers_with_data:
                clear_tier(root, tier_id)
            print(f"Cleared: {', '.join(tiers_with_data)}")

    next_ts = [get_max_ts_num(root) + 1]
    next_ann = [max(get_max_ann_num(root), get_last_used_ann_id(root)) + 1]

    for tier_id in tiers_present:
        n = add_tier_annotations(root, tier_id, segments, next_ts, next_ann)
        print(f"  {tier_id}: added {n} annotation(s)")

    sort_time_order(root)
    set_last_used_ann_id(root, next_ann[0] - 1)

    # Write back with XML declaration and pretty indentation
    indent_xml(root)
    tree.write(args.eaf_file, encoding="unicode", xml_declaration=False)

    # Prepend the XML declaration manually to match ELAN's expected format
    with open(args.eaf_file, "r", encoding="utf-8") as file:
        content = file.read()
    with open(args.eaf_file, "w", encoding="utf-8") as file:
        file.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        file.write(content)

    print(f"\nSaved: {args.eaf_file}")


if __name__ == "__main__":
    main()
