"""Auto-annotation pipeline: visual (Gemini) and ASR transcription."""
from __future__ import annotations

import importlib.util
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy.orm import Session

from irl_corpus.db.models import Task
from irl_corpus.pipeline import auto as auto_pipeline
from irl_corpus.pipeline import transcribe as transcribe_pipeline
from irl_corpus.storage import Storage

# Path to the scripts/ directory (4 levels up from this file)
_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent / "scripts"

# Search order for the annotations JSON
_ANN_JSON_CANDIDATES = [
    _SCRIPTS_DIR.parent / "annotations" / "annotations-v0.1.0-20260510.json",
    _SCRIPTS_DIR.parent / "annotations-v0.1.0-20260510.json",
]


def _default_ann_json() -> Path | None:
    """Return the first existing annotations JSON candidate, or None."""
    for candidate in _ANN_JSON_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _load_script(name: str):
    """Import a script from scripts/ as a Python module."""
    script_path = _SCRIPTS_DIR / f"{name}.py"
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    spec = importlib.util.spec_from_file_location(name, script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_visual_annotation(
    task: Task,
    storage: Storage,
    local_recording: Path,
    *,
    model: str = "gemini-2.5-flash",
    fps: float = 2.0,
    replace: bool = True,
    ann_json_path: Path | None = None,
    session: Session | None = None,
) -> None:
    """Run Gemini visual annotation on a task's segment and upload the updated EAF.

    Requires GEMINI_API_KEY environment variable and ffmpeg on PATH.
    Raises on any failure; caller should handle exceptions.
    Sets task.visual_annotated=True on success if session is provided.
    """
    if task.base_eaf_path is None:
        raise ValueError("Task has no base_eaf_path.")

    seg = task.segment

    if ann_json_path is None:
        ann_json_path = _default_ann_json()
    if ann_json_path is None or not ann_json_path.exists():
        raise FileNotFoundError(
            "Annotations JSON not found. Expected at:\n"
            + "\n".join(f"  {p}" for p in _ANN_JSON_CANDIDATES)
        )

    annotate_mod = _load_script("annotate_visual")
    add_mod = _load_script("add_visual_annotations_to_eaf")

    categories = annotate_mod.load_categories(str(ann_json_path))

    try:
        with tempfile.TemporaryDirectory(prefix="irl_vis_") as tmp_dir:
            tmp = Path(tmp_dir)

            clip_path = tmp / "segment_clip.mp4"
            auto_pipeline.extract_segment_clip(seg, local_recording, clip_path)

            # Re-encode to fps-reduced libx264 MP4 so Gemini accepts it.
            # Stream-copied TS segments use codecs that the Files API may reject.
            reduced_path = tmp / "segment_reduced.mp4"
            annotate_mod.reduce_fps(str(clip_path), fps, str(reduced_path))

            local_eaf = tmp / "base.eaf"
            storage.get(task.base_eaf_path, local_eaf)

            result = annotate_mod.annotate_video(str(reduced_path), model, categories)
            segments_data = result.get("annotations", [])

            ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
            tree = ET.parse(local_eaf)
            root = tree.getroot()

            if replace:
                for tier_id in annotate_mod.VISUAL_TIERS:
                    add_mod.clear_tier(root, tier_id)

            next_ts = [add_mod.get_max_ts_num(root) + 1]
            next_ann = [
                max(add_mod.get_max_ann_num(root), add_mod.get_last_used_ann_id(root))
                + 1
            ]
            for tier_id in annotate_mod.VISUAL_TIERS:
                add_mod.add_tier_annotations(
                    root, tier_id, segments_data, next_ts, next_ann
                )

            add_mod.sort_time_order(root)
            add_mod.set_last_used_ann_id(root, next_ann[0] - 1)
            add_mod.indent_xml(root)

            annotated_eaf = tmp / "annotated.eaf"
            tree.write(str(annotated_eaf), encoding="unicode", xml_declaration=False)
            with open(annotated_eaf, "r", encoding="utf-8") as eaf_file:
                content = eaf_file.read()
            with open(annotated_eaf, "w", encoding="utf-8") as eaf_file:
                eaf_file.write('<?xml version="1.0" encoding="UTF-8"?>\n')
                eaf_file.write(content)

            storage.put(annotated_eaf, task.base_eaf_path)

        if session is not None:
            task.visual_annotated = True
            task.visual_error = None
            session.flush()
    except Exception:
        if session is not None:
            import sys  # pylint: disable=import-outside-toplevel

            task.visual_error = str(sys.exc_info()[1])
            session.flush()
        raise


def run_transcription(
    task: Task,
    storage: Storage,
    local_recording: Path,
    api_url: str,
    *,
    lang: str = "fr",
    translate_lang: str = "",
    chunk_seconds: int = 240,
    clear_existing: bool = True,
    session: Session | None = None,
) -> None:
    """Run ASR transcription on a task's segment and upload the updated EAF.

    Requires ffmpeg on PATH and the transcription API to be reachable.
    Raises on any failure; caller should handle exceptions.
    Sets task.asr_transcribed=True on success if session is provided.
    """
    if task.base_eaf_path is None:
        raise ValueError("Task has no base_eaf_path.")

    seg = task.segment

    add_mod = _load_script("add_transcripts_to_eaf")

    try:
        with tempfile.TemporaryDirectory(prefix="irl_asr_") as tmp_dir:
            tmp = Path(tmp_dir)

            wav_path = tmp / "segment.wav"
            auto_pipeline.extract_segment_wav(seg, local_recording, wav_path)

            transcript_data = transcribe_pipeline.transcribe_wav(
                wav_path,
                api_url,
                lang=lang,
                translate_lang=translate_lang,
                chunk_seconds=chunk_seconds,
                work_dir=tmp / "chunks",
            )
            transcript_segments = transcript_data.get("segments", [])

            local_eaf = tmp / "base.eaf"
            storage.get(task.base_eaf_path, local_eaf)

            tree = ET.parse(local_eaf)
            root = tree.getroot()

            time_order = add_mod.ensure_time_order(root)
            lt_ref = add_mod.ensure_linguistic_type(root)
            transcript_tier = add_mod.ensure_tier(root, "transcript", lt_ref)
            translation_tier = add_mod.ensure_tier(root, "translation", lt_ref)

            if clear_existing:
                add_mod.clear_tier(transcript_tier)
                add_mod.clear_tier(translation_tier)

            next_ts_id = add_mod.next_numeric_id(root, "ts", "TIME_SLOT_ID")
            next_ann_id = add_mod.next_numeric_id(root, "a", "ANNOTATION_ID")

            for seg_data in transcript_segments:
                start_ms = int(round(float(seg_data["start"]) * 1000))
                end_ms = int(round(float(seg_data["end"]) * 1000))
                start_slot = f"ts{next_ts_id}"
                end_slot = f"ts{next_ts_id + 1}"
                next_ts_id += 2
                add_mod.add_time_slot(time_order, start_slot, start_ms)
                add_mod.add_time_slot(time_order, end_slot, end_ms)
                text = str(seg_data.get("text", "")).strip()
                translated = str(seg_data.get("translated_text", "")).strip()
                if text:
                    add_mod.add_segment_annotation(
                        transcript_tier, text, f"a{next_ann_id}", start_slot, end_slot
                    )
                    next_ann_id += 1
                if translated:
                    add_mod.add_segment_annotation(
                        translation_tier,
                        translated,
                        f"a{next_ann_id}",
                        start_slot,
                        end_slot,
                    )
                    next_ann_id += 1

            add_mod.indent_xml(root)
            annotated_eaf = tmp / "transcribed.eaf"
            tree.write(str(annotated_eaf), encoding="UTF-8", xml_declaration=True)

            storage.put(annotated_eaf, task.base_eaf_path)

        if session is not None:
            task.asr_transcribed = True
            task.asr_error = None
            session.flush()
    except Exception:
        if session is not None:
            import sys  # pylint: disable=import-outside-toplevel

            task.asr_error = str(sys.exc_info()[1])
            session.flush()
        raise
