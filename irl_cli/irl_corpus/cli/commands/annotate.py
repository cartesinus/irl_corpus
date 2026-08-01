"""CLI commands: irl annotate ..."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich import print  # pylint: disable=redefined-builtin

from irl_corpus.cli.ctx import get_config, get_factory, get_storage
from irl_corpus.db import session_scope
from irl_corpus.db.models import Recording
from irl_corpus.db.queries import list_tasks, require_recording, require_task
from irl_corpus.pipeline import annotate as annotate_pipeline

app = typer.Typer(help="Auto-annotation commands.", no_args_is_help=True)


def _get_task_context(session, task_id: str, storage):
    """Return (task, segment, local_recording_path)."""
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

    return task, seg, local_recording


@app.command("visual")
def annotate_visual(
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
        None, "--annotations", help="Path to annotations JSON (default: project root)."
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
        try:
            task, seg, local_recording = _get_task_context(session, task_id, storage)
        except (ValueError, FileNotFoundError) as exc:
            print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        if task.base_eaf_path is None:
            print(
                "[red]Task has no base EAF path. Run 'irl recording split' first.[/red]"
            )
            raise typer.Exit(1)

        print(
            f"[cyan]Annotating visual[/cyan] segment {seg.id[:8]} "
            f"({task.base_eaf_path}) with {model}…"
        )
        try:
            annotate_pipeline.run_visual_annotation(
                task,
                storage,
                local_recording,
                model=model,
                fps=fps,
                replace=replace,
                ann_json_path=annotations_json,
            )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[red]Visual annotation failed:[/red] {exc}")
            raise typer.Exit(1) from exc

    print(f"[green]Visual annotation complete.[/green] EAF: {task.base_eaf_path}")


@app.command("transcribe")
def annotate_transcribe(  # pylint: disable=too-many-arguments
    task_id: str = typer.Argument(..., help="Task ID to transcribe."),
    engine: str = typer.Option(
        "legacy",
        "--engine",
        help=(
            "ASR engine: 'legacy' (self-hosted WhisperX-style API) or "
            "'elevenlabs' (Scribe v2 batch: transcribe + diarize + VAD in "
            "one call)."
        ),
    ),
    lang: str = typer.Option("fr", "--lang", "-l", help="Source language code."),
    translate_lang: str = typer.Option(
        "",
        "--translate-lang",
        help="Translation target language, legacy engine only (empty = no translation).",
    ),
    chunk_seconds: int = typer.Option(
        240,
        "--chunk-seconds",
        help="Audio chunk length in seconds (legacy engine only).",
    ),
    api_url: Optional[str] = typer.Option(
        None,
        "--api-url",
        help="Transcription API endpoint, legacy engine only "
        "(overrides [services] transcribe_url in irl.toml).",
    ),
    num_speakers: Optional[int] = typer.Option(
        None,
        "--num-speakers",
        help="Maximum number of speakers to detect (elevenlabs engine only).",
    ),
    clear_existing: bool = typer.Option(
        True, "--clear/--no-clear", help="Clear existing transcript tiers first."
    ),
):
    """Run ASR transcription on a task's segment and inject into EAF.

    Requires ffmpeg on PATH. The legacy engine populates the 'transcript'
    and 'translation' tiers via the configured transcribe_url; the
    elevenlabs engine populates 'transcript' and 'speaker_turn' via the
    ElevenLabs Scribe v2 batch API (requires ELEVENLABS_API_KEY).
    """
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)

    if engine == "elevenlabs":
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            print("[red]ELEVENLABS_API_KEY environment variable is not set.[/red]")
            raise typer.Exit(1)
    elif engine == "legacy":
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
    else:
        print(f"[red]Unknown engine '{engine}'. Use 'legacy' or 'elevenlabs'.[/red]")
        raise typer.Exit(1)

    with session_scope(factory) as session:
        try:
            task, seg, local_recording = _get_task_context(session, task_id, storage)
        except (ValueError, FileNotFoundError) as exc:
            print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

        if task.base_eaf_path is None:
            print(
                "[red]Task has no base EAF path. Run 'irl recording split' first.[/red]"
            )
            raise typer.Exit(1)

        try:
            if engine == "elevenlabs":
                print(f"[cyan]Transcribing (Scribe v2)[/cyan] segment {seg.id[:8]}…")
                annotate_pipeline.run_transcription_elevenlabs(
                    task,
                    storage,
                    local_recording,
                    api_key,
                    language_code=lang,
                    num_speakers=num_speakers,
                    clear_existing=clear_existing,
                )
            else:
                print(
                    f"[cyan]Transcribing[/cyan] segment {seg.id[:8]} "
                    f"(lang={lang}, translate→{translate_lang})…"
                )
                annotate_pipeline.run_transcription(
                    task,
                    storage,
                    local_recording,
                    resolved_api_url,
                    lang=lang,
                    translate_lang=translate_lang,
                    chunk_seconds=chunk_seconds,
                    clear_existing=clear_existing,
                )
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[red]Transcription failed:[/red] {exc}")
            raise typer.Exit(1) from exc

    print(f"[green]Transcription complete.[/green] EAF: {task.base_eaf_path}")


@app.command("auto")
def annotate_auto(
    recording_id: str = typer.Argument(..., help="Recording ID (or prefix)."),
    lang: str = typer.Option("fr", "--lang", "-l", help="Source language for ASR."),
    translate_lang: str = typer.Option(
        "",
        "--translate-lang",
        help="Translation target language (empty = no translation).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Re-run even if already marked done.",
    ),
):
    """Run (or re-run) visual + ASR annotation for all tasks in a recording.

    Skips steps already marked done unless --force is given.
    Safe to run multiple times — acts as a resume mechanism.
    """
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)

    resolved_api_url = cfg.services.transcribe_url
    gemini_key = os.environ.get("GEMINI_API_KEY")
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")

    with session_scope(factory) as session:
        try:
            rec = require_recording(session, recording_id)
        except ValueError as exc:
            print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        tasks = [
            task
            for task in list_tasks(session, recording_id=rec.id)
            if task.type == "annotation"
            and task.base_eaf_path is not None
            and task.superseded_by is None
        ]

        if not tasks:
            print("[dim]No annotation tasks with EAF found for this recording.[/dim]")
            return

        local_recording = storage.local_path(rec.storage_path)
        if local_recording is None:
            print("[red]Auto-annotation requires local storage.[/red]")
            raise typer.Exit(1)

        print(
            f"[bold]{rec.title}[/bold]  {len(tasks)} task(s)"
            + (
                "  [yellow](--force: re-running completed steps)[/yellow]"
                if force
                else ""
            )
        )

        for task in tasks:
            if force:
                task.visual_annotated = False
                task.asr_transcribed = False

            # Visual
            if task.visual_annotated:
                print(f"  [dim]visual {task.segment_id[:8]}: already done[/dim]")
            elif task.visual_error and not force:
                print(
                    f"  [yellow]visual {task.segment_id[:8]}: last error:[/yellow] "
                    f"{task.visual_error}"
                )
                print("    (run with --force to retry)")
            elif gemini_key:
                print(f"[cyan]Visual[/cyan] {task.segment_id[:8]}…")
                try:
                    annotate_pipeline.run_visual_annotation(
                        task, storage, local_recording, session=session
                    )
                    print("  [green]done[/green]")
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"  [yellow]failed:[/yellow] {exc}")
            else:
                print(
                    f"  [dim]visual {task.segment_id[:8]}: skipped (GEMINI_API_KEY not set)[/dim]"
                )

            # ASR
            if task.asr_transcribed:
                print(f"  [dim]ASR {task.segment_id[:8]}: already done[/dim]")
            elif task.asr_error and not force:
                print(
                    f"  [yellow]ASR {task.segment_id[:8]}: last error:[/yellow] {task.asr_error}"
                )
                print("    (run with --force to retry)")
            elif elevenlabs_key:
                print(f"[cyan]ASR (Scribe v2)[/cyan] {task.segment_id[:8]}…")
                try:
                    annotate_pipeline.run_transcription_elevenlabs(
                        task,
                        storage,
                        local_recording,
                        elevenlabs_key,
                        language_code=lang,
                        session=session,
                    )
                    print("  [green]done[/green]")
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"  [yellow]failed:[/yellow] {exc}")
            elif resolved_api_url:
                print(f"[cyan]ASR[/cyan] {task.segment_id[:8]}…")
                try:
                    annotate_pipeline.run_transcription(
                        task,
                        storage,
                        local_recording,
                        resolved_api_url,
                        lang=lang,
                        translate_lang=translate_lang,
                        session=session,
                    )
                    print("  [green]done[/green]")
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"  [yellow]failed:[/yellow] {exc}")
            else:
                print(
                    f"  [dim]ASR {task.segment_id[:8]}: skipped "
                    "(neither ELEVENLABS_API_KEY nor transcribe_url configured)[/dim]"
                )
