"""CLI commands: irl segment ..."""
from __future__ import annotations

from typing import Optional

import typer
from rich import print  # pylint: disable=redefined-builtin
from rich import box
from rich.table import Table

from irl_corpus.cli.ctx import get_config, get_factory, whoami
from irl_corpus.cli.utils import fmt_ms
from irl_corpus.db import session_scope
from irl_corpus.db.models import Segment as SegModel
from irl_corpus.db.queries import get_chain, require_recording, require_segment
from irl_corpus.pipeline import sequence

app = typer.Typer(help="Manage segment sequences.", no_args_is_help=True)

_STATUS_STYLE = {
    "pending": "dim",
    "in_annotation": "yellow",
    "in_validation": "blue",
    "needs_rework": "red",
    "validated": "green",
}


@app.command("list")
def segment_list(
    recording_id: str = typer.Argument(..., help="Recording ID."),
    show_removed: bool = typer.Option(
        False, "--removed", help="Also show removed segments."
    ),
):
    """List the ordered segment chain for a recording."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        rec = require_recording(session, recording_id)
        chain = get_chain(session, recording_id)

        removed = []
        if show_removed:
            removed = (
                session.query(SegModel)
                .filter_by(recording_id=recording_id, status="removed")
                .all()
            )

    print(f"[bold]{rec.title}[/bold]  [dim]{rec.id}[/dim]")
    print()

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Type")
    table.add_column("Range")
    table.add_column("Duration", justify="right")
    table.add_column("Annotation status")
    table.add_column("Notes")

    for idx, seg in enumerate(chain, 1):
        dur_ms = (
            (seg.end_ms or 0) - (seg.start_ms or 0)
            if seg.start_ms is not None
            else None
        )
        dur_str = f"{(dur_ms or 0) // 60_000}m" if dur_ms else "?"
        style = _STATUS_STYLE.get(seg.annotation_status, "")
        seg_type = "[dim]gap[/dim]" if seg.type == "gap" else "content"
        table.add_row(
            str(idx),
            seg.id[:8],
            seg_type,
            f"{fmt_ms(seg.start_ms)} – {fmt_ms(seg.end_ms)}",
            dur_str,
            f"[{style}]{seg.annotation_status}[/{style}]"
            if style
            else seg.annotation_status,
            seg.notes or "",
        )

    for seg in removed:
        table.add_row(
            "-",
            seg.id[:8],
            f"[dim]{seg.type} (removed)[/dim]",
            f"{fmt_ms(seg.start_ms)} – {fmt_ms(seg.end_ms)}",
            "",
            "",
            seg.notes or "",
        )

    print(table)


@app.command("show")
def segment_show(
    segment_id: str = typer.Argument(..., help="Segment ID."),
):
    """Show details and task history for a segment."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        seg = require_segment(session, segment_id)
        tasks = seg.tasks

        print(f"[bold]Segment[/bold] [cyan]{seg.id}[/cyan]")
        print(f"  recording:  {seg.recording_id}")
        print(f"  type:       {seg.type}")
        print(f"  range:      {fmt_ms(seg.start_ms)} – {fmt_ms(seg.end_ms)}")
        print(f"  status:     {seg.status}")
        print(f"  annotation: {seg.annotation_status}")
        if seg.notes:
            print(f"  notes:      {seg.notes}")
        if seg.split_from_segment_id:
            print(f"  split from: {seg.split_from_segment_id[:8]}")
        print(f"  prev:       {(seg.prev_segment_id or '')[:8] or '-'}")
        print(f"  next:       {(seg.next_segment_id or '')[:8] or '-'}")

        if tasks:
            print()
            print("[bold]Tasks:[/bold]")
            for task in sorted(tasks, key=lambda x: x.created_at):
                print(
                    f"  {task.id[:8]}  {task.type:10s}  round={task.round}"
                    f"  status={task.status:12s}"
                    f"  assigned={task.assigned_to or '-'}"
                )


@app.command("split")
def segment_split(
    segment_id: str = typer.Argument(..., help="Segment ID to split."),
    at_ms: int = typer.Option(
        ..., "--at-ms", help="Split point in ms (offset in original recording)."
    ),
    gap_to_ms: Optional[int] = typer.Option(
        None, "--gap-to-ms", help="If set, creates a gap from --at-ms to this value."
    ),
    gap_notes: Optional[str] = typer.Option(
        None, "--gap-notes", help="Notes for the gap segment."
    ),
):
    """Split a segment in two, optionally with a gap in between."""
    cfg = get_config()
    factory = get_factory(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        if gap_to_ms is not None:
            left, gap, right = sequence.split_segment_with_gap(
                session,
                segment_id,
                at_ms,
                gap_to_ms,
                user_name=user,
                gap_notes=gap_notes,
            )
            print(
                f"[green]Split[/green] {segment_id[:8]} into "
                f"[cyan]{left.id[:8]}[/cyan] + "
                f"[dim]gap:{gap.id[:8]}[/dim] + "
                f"[cyan]{right.id[:8]}[/cyan]"
            )
        else:
            left, right = sequence.split_segment(
                session, segment_id, at_ms, user_name=user
            )
            print(
                f"[green]Split[/green] {segment_id[:8]} into "
                f"[cyan]{left.id[:8]}[/cyan] + [cyan]{right.id[:8]}[/cyan]"
            )


@app.command("remove")
def segment_remove(
    segment_id: str = typer.Argument(..., help="Segment ID to remove."),
    reason: Optional[str] = typer.Option(
        None, "--reason", "-r", help="Reason for removal."
    ),
    no_gap: bool = typer.Option(
        False, "--no-gap", help="Don't insert a gap placeholder."
    ),
):
    """Remove a segment (inserts a gap placeholder by default)."""
    cfg = get_config()
    factory = get_factory(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        seg = sequence.remove_segment(
            session,
            segment_id,
            user_name=user,
            reason=reason,
            replace_with_gap=not no_gap,
        )
        print(f"[yellow]Removed[/yellow] segment {seg.id[:8]}")
        if not no_gap:
            print("  A gap placeholder was inserted in its place.")
