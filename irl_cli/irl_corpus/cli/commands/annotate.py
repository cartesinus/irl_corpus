"""CLI commands: irl annotate ..."""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path
from typing import Optional

import typer
from rich import print  # pylint: disable=redefined-builtin

from irl_corpus.cli.ctx import get_config, get_factory, get_storage
from irl_corpus.db import session_scope
from irl_corpus.db.queries import require_task
from irl_corpus.pipeline import auto as auto_pipeline
from irl_corpus.pipeline import transcribe as transcribe_pipeline

app = typer.Typer(help="Auto-annotation commands.", no_args_is_help=True)

# Path to the scripts/ directory (two levels above the irl_cli/ package root)
_SCRIPTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "scripts"


def _load_script(name: str):
    """Import a script from scripts/ as a Python module."""
    script_path = _SCRIPTS_DIR / f"{name}.py"
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")
    spec = importlib.util.spec_from_file_location(name, script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_task_context(session, task_id: str, storage):
    """Return (task, segment, recording_local_path, local_eaf_path_or_None)."""
    from irl_corpus.db.models import (
        Recording,
    )  # pylint: disable=import-outside-toplevel

    task = require_task(session, task_id)
    seg = task.segment
    rec = session.get(Recording, seg.recording_id)
    if rec is None:
        raise ValueError(f"Recording for segment {seg.id} not found.")

    local_recording = storage.local_path(rec.storage_path)
    if local_recording is None:
        raise ValueError(
            "Annotation commands currently require local storage. "
            "S3 remote storage is not yet supported for auto-annotation."
        )

    if not local_recording.exists():
        raise FileNotFoundError(f"Recording file not found: {local_recording}")

    return task, seg, rec, local_recording


@app.command("visual")
def annotate_visual(  # pylint: disable=too-many-locals
    task_id: str = typer.Argument(..., help="Task ID to annotate."),
    model: str = typer.Option(
        "gemini-2.5-flash", "--model", "-m", help="Gemini model name."
    ),
    fps: float = typer.Option(
        2.0, "--fps", help="Frame rate for video sent to Gemini."
    ),
    replace: bool = typer.Option(
        True,
        "--replace/--no-replace",
        help="Replace existing visual tier annotations (default: on).",
    ),
    annotations_json: Optional[Path] = typer.Option(
        None, "--annotations", help="Path to annotations JSON (default: annotations dir)."
    ),
):
    """Run Gemini visual scene annotation on a task's segment.

    Requires GEMINI_API_KEY environment variable and ffmpeg on PATH.
    Annotates tiers: acoustic_scene, visual_context, speaker_visibility, topic.
    """
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)

    with session_scope(factory) as session:
        task, seg, _rec, local_recording = _get_task_context(session, task_id, storage)

        if task.base_eaf_path is None:
            print(
                "[red]Task has no base EAF path. Run 'irl recording split' first.[/red]"
            )
            raise typer.Exit(1)

        # Resolve annotations JSON
        ann_json = annotations_json
        if ann_json is None:
            candidate = _SCRIPTS_DIR.parent / "annotations/annotations-v0.1.0-20260510.json"
            ann_json = candidate if candidate.exists() else None

        print(
            f"[cyan]Annotating visual[/cyan] segment {seg.id[:8]} "
            f"({task.base_eaf_path}) with {model}…"
        )

        with tempfile.TemporaryDirectory(prefix="irl_annotate_") as tmp_dir:
            tmp = Path(tmp_dir)

            # Extract segment clip for Gemini
            print("[dim]Extracting segment clip…[/dim]")
            clip_path = tmp / "segment_clip.mp4"
            try:
                auto_pipeline.extract_segment_clip(seg, local_recording, clip_path)
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[red]Clip extraction failed:[/red] {exc}")
                raise typer.Exit(1) from exc

            # Download base EAF to temp dir
            local_eaf = tmp / "base.eaf"
            storage.get(task.base_eaf_path, local_eaf)

            # Run visual annotation via script module
            try:
                annotate_mod = _load_script("annotate_visual")
                add_mod = _load_script("add_visual_annotations_to_eaf")
            except FileNotFoundError as exc:
                print(f"[red]Script not found:[/red] {exc}")
                raise typer.Exit(1) from exc

            ann_path = ann_json or (
                _SCRIPTS_DIR.parent / "annotations/annotations-v0.1.0-20260510.json"
            )
            if not ann_path.exists():
                print(f"[red]Annotations JSON not found:[/red] {ann_path}")
                raise typer.Exit(1)

            print("[dim]Loading annotation schema…[/dim]")
            categories = annotate_mod.load_categories(str(ann_path))

            print(f"[dim]Sending to Gemini ({model})…[/dim]")
            try:
                result = annotate_mod.annotate_video(str(clip_path), model, categories)
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[red]Gemini annotation failed:[/red] {exc}")
                raise typer.Exit(1) from exc

            segments_data = result.get("annotations", [])
            print(f"  → {len(segments_data)} annotation segment(s) returned")

            # Inject into EAF
            import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel

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
                count = add_mod.add_tier_annotations(
                    root, tier_id, segments_data, next_ts, next_ann
                )
                print(f"  {tier_id}: {count} annotation(s)")

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

            # Upload annotated EAF back to storage (overwrite base)
            storage.put(annotated_eaf, task.base_eaf_path)

        print(f"[green]Visual annotation complete.[/green] EAF: {task.base_eaf_path}")


@app.command("transcribe")
def annotate_transcribe(  # pylint: disable=too-many-locals
    task_id: str = typer.Argument(..., help="Task ID to transcribe."),
    lang: str = typer.Option("fr", "--lang", "-l", help="Source language code."),
    translate_lang: str = typer.Option(
        "en", "--translate-lang", help="Translation target language."
    ),
    chunk_seconds: int = typer.Option(
        240, "--chunk-seconds", help="Audio chunk length in seconds."
    ),
    api_url: Optional[str] = typer.Option(
        None,
        "--api-url",
        help="Transcription API endpoint (overrides [services] transcribe_url in irl.toml).",
    ),
    clear_existing: bool = typer.Option(
        True, "--clear/--no-clear", help="Clear existing transcript tiers first."
    ),
):
    """Run ASR transcription on a task's segment and inject into EAF.

    Requires ffmpeg on PATH and access to the transcription API.
    Populates the 'transcript' and 'translation' tiers.
    """
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)

    # Resolve transcription URL: CLI flag → config → error
    resolved_api_url = api_url or cfg.services.transcribe_url
    if not resolved_api_url:
        print(
            "[red]No transcription URL configured.[/red]\n"
            "Set it in irl.toml:\n"
            "  [services]\n"
            '  transcribe_url = "https://..."\n'
            "or via environment variable: IRL_TRANSCRIBE_URL"
        )
        raise typer.Exit(1)

    with session_scope(factory) as session:
        task, seg, _rec, local_recording = _get_task_context(session, task_id, storage)

        if task.base_eaf_path is None:
            print(
                "[red]Task has no base EAF path. Run 'irl recording split' first.[/red]"
            )
            raise typer.Exit(1)

        print(
            f"[cyan]Transcribing[/cyan] segment {seg.id[:8]} "
            f"(lang={lang}, translate→{translate_lang})…"
        )

        with tempfile.TemporaryDirectory(prefix="irl_transcribe_") as tmp_dir:
            tmp = Path(tmp_dir)

            # Extract WAV
            print("[dim]Extracting audio…[/dim]")
            wav_path = tmp / "segment.wav"
            try:
                auto_pipeline.extract_segment_wav(seg, local_recording, wav_path)
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[red]WAV extraction failed:[/red] {exc}")
                raise typer.Exit(1) from exc

            # Run transcription
            print("[dim]Transcribing audio chunks…[/dim]")
            try:
                transcript_data = transcribe_pipeline.transcribe_wav(
                    wav_path,
                    resolved_api_url,
                    lang=lang,
                    translate_lang=translate_lang,
                    chunk_seconds=chunk_seconds,
                    work_dir=tmp / "transcription_work",
                )
            except Exception as exc:  # pylint: disable=broad-except
                print(f"[red]Transcription failed:[/red] {exc}")
                raise typer.Exit(1) from exc

            transcript_segments = transcript_data.get("segments", [])

            # Download base EAF and inject transcripts
            local_eaf = tmp / "base.eaf"
            storage.get(task.base_eaf_path, local_eaf)

            annotated_eaf = tmp / "transcribed.eaf"
            try:
                add_mod = _load_script("add_transcripts_to_eaf")
            except FileNotFoundError as exc:
                print(f"[red]Script not found:[/red] {exc}")
                raise typer.Exit(1) from exc

            import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel

            tree = ET.parse(local_eaf)
            root = tree.getroot()
            print(f"  → {len(transcript_segments)} transcript segment(s)")

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
            tree.write(str(annotated_eaf), encoding="UTF-8", xml_declaration=True)

            storage.put(annotated_eaf, task.base_eaf_path)

        print(f"[green]Transcription complete.[/green] EAF: {task.base_eaf_path}")
