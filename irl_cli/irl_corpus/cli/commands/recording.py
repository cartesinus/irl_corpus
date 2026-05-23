"""CLI commands: irl recording ..."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

import typer
from rich import print  # pylint: disable=redefined-builtin
from rich import box
from rich.table import Table

from irl_corpus.cli.ctx import get_config, get_factory, get_storage, whoami
from irl_corpus.db import session_scope
from irl_corpus.db.queries import (
    get_current_schema,
    list_recordings,
    require_recording,
)
from irl_corpus.pipeline import auto as auto_pipeline
from irl_corpus.pipeline import ingest
from irl_corpus.pipeline import tasks as task_pipeline

app = typer.Typer(help="Manage recordings.", no_args_is_help=True)

_30_MIN_MS = 30 * 60 * 1000


def _fmt_duration(millis: int | None) -> str:
    if millis is None:
        return "unknown"
    hours, remainder = divmod(millis // 1000, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@app.command("add")
def recording_add(  # pylint: disable=too-many-locals,too-many-statements
    file: Path = typer.Argument(..., help="Path to the video/audio file."),
    title: Optional[str] = typer.Option(
        None, "--title", "-t", help="Title (defaults to filename)."
    ),
    duration_ms: Optional[int] = typer.Option(
        None, "--duration-ms", help="Override auto-detected duration (milliseconds)."
    ),
    transcode: bool = typer.Option(
        True,
        "--transcode/--no-transcode",
        help="Convert .ts files to .mp4 before upload (default: on).",
    ),
    segment_duration_ms: int = typer.Option(
        _30_MIN_MS, "--segment-ms", help="Segment length in ms (default 30 min)."
    ),
    create_eaf: bool = typer.Option(
        True, "--eaf/--no-eaf", help="Create base EAF files for each segment."
    ),
):
    """Upload a recording, auto-split into segments, and create base EAF files.

    Duration is detected automatically via ffprobe.
    MPEG-TS (.ts) files are transcoded to MP4 (stream-copy) before upload.
    Recordings longer than --segment-ms are split into fixed-length segments.
    A base EAF (empty tiers, media link) is stored for each content segment.
    """
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)
    user = whoami(cfg)

    if title is None:
        title = file.stem

    upload_file = file
    tmp_dir = None

    # Transcode .ts → .mp4 if requested
    if transcode and file.suffix.lower() == ".ts":
        print(
            f"[cyan]Transcoding[/cyan] {file.name} → MP4 (stream-copy, no re-encoding)…"
        )
        tmp_dir = tempfile.mkdtemp(prefix="irl_transcode_")
        mp4_path = Path(tmp_dir) / (file.stem + ".mp4")
        try:
            ingest.transcode_to_mp4(file, mp4_path)
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[red]Transcode failed:[/red] {exc}")
            raise typer.Exit(1) from exc
        upload_file = mp4_path
        print(f"  → [green]done[/green] ({mp4_path.name})")

    # Auto-probe duration unless overridden
    final_duration_ms = duration_ms
    if final_duration_ms is None:
        print("[cyan]Probing duration[/cyan] with ffprobe…")
        final_duration_ms = ingest.probe_duration_ms(upload_file)
        if final_duration_ms is not None:
            print(f"  → {_fmt_duration(final_duration_ms)}")
        else:
            print("  [yellow]ffprobe unavailable — duration unknown[/yellow]")

    try:
        with session_scope(factory) as session:
            # Upload
            rec = ingest.upload_recording(
                session, storage, upload_file, title, user_name=user
            )
            if final_duration_ms is not None:
                ingest.set_recording_duration(session, rec.id, final_duration_ms)

            print(f"[green]Uploaded[/green] recording [bold]{rec.id}[/bold] — {title}")
            print(f"  storage path: {rec.storage_path}")
            if final_duration_ms:
                print(f"  duration:     {_fmt_duration(final_duration_ms)}")

            # Split into segments (only if we know the duration)
            if final_duration_ms is None:
                print(
                    "[yellow]Skipping split — duration unknown. "
                    "Run 'irl recording split' manually.[/yellow]"
                )
                return

            if final_duration_ms <= segment_duration_ms:
                # Single segment — treat the whole recording as one segment
                segments = ingest.split_into_segments(
                    session,
                    rec.id,
                    user_name=user,
                    segment_duration_ms=final_duration_ms,  # one chunk = whole file
                )
            else:
                segments = ingest.split_into_segments(
                    session,
                    rec.id,
                    user_name=user,
                    segment_duration_ms=segment_duration_ms,
                )

            content_segs = [seg for seg in segments if seg.type == "content"]
            print(
                f"[green]Split into {len(content_segs)} segment(s)[/green]"
                + (
                    f" × {segment_duration_ms // 60_000} min"
                    if len(content_segs) > 1
                    else ""
                )
            )

            # Create annotation tasks
            schema = get_current_schema(session)
            if schema is None:
                print(
                    "[yellow]No current schema — skipping task and EAF creation. "
                    "Run 'irl schema import' then 'irl recording split'.[/yellow]"
                )
                return

            new_tasks = task_pipeline.create_tasks_for_recording(session, rec.id)
            print(f"[green]Created {len(new_tasks)} annotation task(s)[/green]")

            # Build base EAF for each content segment
            if create_eaf:
                print("[cyan]Creating base EAF files…[/cyan]")
                for seg in content_segs:
                    try:
                        eaf_path = auto_pipeline.write_base_eaf(
                            session,
                            seg.id,
                            schema,
                            storage,
                            rec.storage_path,
                        )
                        # Wire the EAF path into the segment's annotation task
                        for task in new_tasks:
                            if task.segment_id == seg.id:
                                task.base_eaf_path = eaf_path
                        start = _fmt_duration(seg.start_ms)
                        end = _fmt_duration(seg.end_ms)
                        print(f"  [dim]{seg.id[:8]}[/dim]  {start}–{end}  → {eaf_path}")
                    except Exception as exc:  # pylint: disable=broad-except
                        print(f"  [red]EAF failed for {seg.id[:8]}:[/red] {exc}")

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@app.command("list")
def recording_list():
    """List all recordings."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        recs = list_recordings(session)

    if not recs:
        print("[dim]No recordings found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("ID", style="cyan", no_wrap=True, max_width=12)
    table.add_column("Title")
    table.add_column("Duration", justify="right")
    table.add_column("Status")
    table.add_column("Uploaded")

    for rec in recs:
        table.add_row(
            rec.id[:8],
            rec.title,
            _fmt_duration(rec.duration_ms),
            rec.status,
            rec.uploaded_at.strftime("%Y-%m-%d"),
        )

    print(table)


@app.command("show")
def recording_show(
    recording_id: str = typer.Argument(..., help="Recording ID (or prefix)."),
):
    """Show details for a recording."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        rec = require_recording(session, recording_id)
        print(f"[bold]{rec.title}[/bold]  [dim]{rec.id}[/dim]")
        print(f"  file:     {rec.original_filename}")
        print(f"  storage:  {rec.storage_path}")
        print(f"  duration: {_fmt_duration(rec.duration_ms)}")
        print(f"  status:   {rec.status}")
        print(f"  uploaded: {rec.uploaded_at.strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  segments: {len(rec.segments)}")


@app.command("split")
def recording_split(
    recording_id: str = typer.Argument(..., help="Recording ID."),
    duration_ms: int = typer.Option(
        _30_MIN_MS, "--duration-ms", "-d", help="Segment duration in ms."
    ),
    create_tasks: bool = typer.Option(
        True, "--tasks/--no-tasks", help="Auto-create annotation tasks."
    ),
    create_eaf: bool = typer.Option(
        True, "--eaf/--no-eaf", help="Create base EAF files for each segment."
    ),
):
    """Split a recording into segments and optionally create annotation tasks + EAFs."""
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        rec = require_recording(session, recording_id)
        segments = ingest.split_into_segments(
            session, recording_id, user_name=user, segment_duration_ms=duration_ms
        )
        content_segs = [seg for seg in segments if seg.type == "content"]
        print(
            f"[green]Created {len(content_segs)} segment(s)[/green] "
            f"from recording {recording_id[:8]}"
        )

        schema = get_current_schema(session)
        new_tasks = []
        if create_tasks and schema:
            new_tasks = task_pipeline.create_tasks_for_recording(session, recording_id)
            print(f"[green]Created {len(new_tasks)} annotation task(s)[/green]")
        elif create_tasks and not schema:
            print("[yellow]No current schema — skipping task creation.[/yellow]")

        if create_eaf and schema:
            print("[cyan]Creating base EAF files…[/cyan]")
            for seg in content_segs:
                try:
                    eaf_path = auto_pipeline.write_base_eaf(
                        session, seg.id, schema, storage, rec.storage_path
                    )
                    for task in new_tasks:
                        if task.segment_id == seg.id:
                            task.base_eaf_path = eaf_path
                    print(
                        f"  [dim]{seg.id[:8]}[/dim]  "
                        f"{_fmt_duration(seg.start_ms)}–{_fmt_duration(seg.end_ms)}"
                        f"  → {eaf_path}"
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"  [red]EAF failed for {seg.id[:8]}:[/red] {exc}")

    for seg in segments:
        mins = ((seg.end_ms or 0) - (seg.start_ms or 0)) // 60_000
        print(
            f"  {seg.id[:8]}  "
            f"{_fmt_duration(seg.start_ms)}–{_fmt_duration(seg.end_ms)}  ({mins}m)"
        )
