"""CLI commands: irl task ..."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich import print  # pylint: disable=redefined-builtin
from rich import box
from rich.table import Table

from irl_corpus.cli.ctx import get_config, get_factory, get_storage, whoami
from irl_corpus.db import session_scope
from irl_corpus.db.models import User
from irl_corpus.db.queries import (
    get_task_chain,
    get_task_saves,
    list_tasks,
    require_task,
)
from irl_corpus.pipeline import fetch as fetch_pipeline
from irl_corpus.pipeline import tasks as task_pipeline

app = typer.Typer(help="Manage annotation tasks.", no_args_is_help=True)

_STATUS_STYLE = {
    "pending": "dim",
    "in_progress": "yellow",
    "done": "blue",
    "approved": "green",
    "rejected": "red",
    "cancelled": "dim",
}


def _user_name(session, user_id: str | None) -> str:
    if not user_id:
        return "-"
    user_obj = session.get(User, user_id)
    return user_obj.name if user_obj else user_id[:8]


@app.command("list")
# pylint: disable=too-many-arguments
def task_list(
    task_type: Optional[str] = typer.Option(
        None, "--type", "-T", help="annotation | validation"
    ),
    status: Optional[str] = typer.Option(None, "--status", "-s"),
    assignee: Optional[str] = typer.Option(
        None, "--assignee", "-a", help="Annotator name."
    ),
    recording_id: Optional[str] = typer.Option(None, "--recording", "-r"),
    segment_id: Optional[str] = typer.Option(None, "--segment"),
    mine: bool = typer.Option(False, "--mine", "-m", help="Only tasks assigned to me."),
):
    """List tasks with optional filters."""
    cfg = get_config()
    factory = get_factory(cfg)

    if mine:
        assignee = whoami(cfg)

    with session_scope(factory) as session:
        results = list_tasks(
            session,
            task_type=task_type,
            status=status,
            assigned_to_name=assignee,
            segment_id=segment_id,
            recording_id=recording_id,
        )

        if not results:
            print("[dim]No tasks found.[/dim]")
            return

        table = Table(box=box.SIMPLE_HEAD)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Type")
        table.add_column("Rnd", justify="right")
        table.add_column("Status")
        table.add_column("Segment", style="dim")
        table.add_column("Assigned")
        table.add_column("Working on")
        table.add_column("Saves", justify="right")

        for task_row in results:
            style = _STATUS_STYLE.get(task_row.status, "")
            save_count = len(task_row.saves)
            table.add_row(
                task_row.id[:8],
                task_row.type,
                str(task_row.round),
                f"[{style}]{task_row.status}[/{style}]" if style else task_row.status,
                task_row.segment_id[:8],
                _user_name(session, task_row.assigned_to),
                _user_name(session, task_row.working_on),
                str(save_count),
            )

    print(table)


@app.command("show")
def task_show(
    task_id: str = typer.Argument(..., help="Task ID."),
):
    """Show full task details including save history and chain."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        task = require_task(session, task_id)
        saves = get_task_saves(session, task_id)
        chain = get_task_chain(session, task_id)

        print(f"[bold]Task[/bold] [cyan]{task.id}[/cyan]")
        print(f"  type:       {task.type}")
        print(f"  status:     {task.status}")
        print(f"  round:      {task.round}")
        print(f"  segment:    {task.segment_id[:8]}")
        print(f"  schema:     {task.schema.version if task.schema else '?'}")
        print(f"  assigned:   {_user_name(session, task.assigned_to)}")
        print(f"  working on: {_user_name(session, task.working_on)}")
        print(f"  created:    {task.created_at.strftime('%Y-%m-%d %H:%M UTC')}")
        if task.completed_at:
            print(f"  completed:  {task.completed_at.strftime('%Y-%m-%d %H:%M UTC')}")
        if task.base_eaf_path:
            print(f"  base EAF:   {task.base_eaf_path}")
        visual_state = (
            "[green]done[/green]" if task.visual_annotated else "[dim]pending[/dim]"
        )
        asr_state = (
            "[green]done[/green]" if task.asr_transcribed else "[dim]pending[/dim]"
        )
        print(f"  visual:     {visual_state}")
        if task.visual_error:
            print(f"    [red]error:[/red] {task.visual_error}")
        print(f"  ASR:        {asr_state}")
        if task.asr_error:
            print(f"    [red]error:[/red] {task.asr_error}")
        if task.superseded_by:
            print(f"  [dim]superseded by: {task.superseded_by[:8]}[/dim]")

        if len(chain) > 1:
            print()
            print("[bold]History chain:[/bold]")
            for chain_task in chain:
                marker = "→ " if chain_task.id == task.id else "  "
                print(
                    f"  {marker}{chain_task.id[:8]}  {chain_task.type:10s}"
                    f"  round={chain_task.round}  {chain_task.status}"
                )

        if saves:
            print()
            print("[bold]Saves:[/bold]")
            for save_entry in saves:
                final_marker = " [green][final][/green]" if save_entry.is_final else ""
                save_user = session.get(User, save_entry.saved_by)
                print(
                    f"  {save_entry.saved_at.strftime('%Y-%m-%d %H:%M')}  "
                    f"{save_user.name if save_user else save_entry.saved_by[:8]}"
                    f"{final_marker}"
                )
                print(f"    {save_entry.eaf_path}")


@app.command("take")
def task_take(
    task_id: str = typer.Argument(..., help="Task ID."),
):
    """Assign task to yourself and mark as in-progress (soft lock)."""
    cfg = get_config()
    factory = get_factory(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        task = task_pipeline.take_task(session, task_id, user)
        print(
            f"[green]Took[/green] task {task.id[:8]} — now assigned to [bold]{user}[/bold]"
        )
        if task.base_eaf_path:
            print(f"  Base EAF: {task.base_eaf_path}")


@app.command("assign")
def task_assign(
    task_id: str = typer.Argument(..., help="Task ID."),
    assignee: str = typer.Option(..., "--to", help="Annotator name."),
):
    """Formally assign a task to a user (admin use)."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        task = task_pipeline.assign_task(session, task_id, assignee)
        print(f"[green]Assigned[/green] task {task.id[:8]} to [bold]{assignee}[/bold]")


@app.command("working")
def task_working(
    task_id: str = typer.Argument(..., help="Task ID."),
):
    """Set yourself as currently working on this task (soft lock)."""
    cfg = get_config()
    factory = get_factory(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        task = task_pipeline.set_working(session, task_id, user)
        print(f"[green]Working on[/green] task {task.id[:8]} as [bold]{user}[/bold]")


@app.command("release")
def task_release(
    task_id: str = typer.Argument(..., help="Task ID."),
):
    """Clear the working-on soft lock (stepping away from the task)."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        task = task_pipeline.release_working(session, task_id)
        print(f"[yellow]Released[/yellow] working lock on task {task.id[:8]}")


@app.command("fetch")
def task_fetch(
    task_id: str = typer.Argument(..., help="Task ID."),
    dest: Path = typer.Option(
        Path("~/irl_work"), "--dest", help="Local working directory."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-download even if the media is already present."
    ),
):
    """Download a task's EAF and its recording for annotating in ELAN."""
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)

    with session_scope(factory) as session:
        plan = fetch_pipeline.plan_fetch(session, task_id)

    print(f"Fetching task {plan.task_id[:8]} ([dim]{plan.eaf_source} EAF[/dim])…")
    result = fetch_pipeline.fetch_task(
        plan, storage, dest.expanduser(), force=force
    )

    if result.media_cached:
        print(f"  media:  [dim]{result.media_path}  (cached)[/dim]")
    else:
        print(f"  media:  {result.media_path}")
    print(f"[green]Open in ELAN:[/green] {result.eaf_path}")


@app.command("save")
def task_save(
    task_id: str = typer.Argument(..., help="Task ID."),
    eaf_file: Path = typer.Argument(..., help="Path to the EAF file to save."),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
):
    """Save an intermediate EAF snapshot (partial work)."""
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        saved = task_pipeline.save_partial(
            session, task_id, eaf_file, user, storage, notes=notes
        )
        print(f"[green]Saved[/green] partial for task {task_id[:8]}")
        print(f"  stored at: {saved.eaf_path}")


@app.command("finish")
def task_finish(
    task_id: str = typer.Argument(..., help="Task ID."),
    eaf_file: Path = typer.Argument(..., help="Path to the final EAF file."),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
):
    """Finish an annotation task. Auto-creates a validation task."""
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        ann_task, val_task = task_pipeline.finish_annotation(
            session, task_id, eaf_file, user, storage, notes=notes
        )
        print(f"[green]Finished[/green] annotation task {ann_task.id[:8]}")
        print(
            f"[blue]Created[/blue] validation task [cyan]{val_task.id[:8]}[/cyan] (pending)"
        )


@app.command("approve")
def task_approve(
    task_id: str = typer.Argument(..., help="Validation task ID."),
):
    """Approve a validation task. Segment becomes fully annotated."""
    cfg = get_config()
    factory = get_factory(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        task = task_pipeline.approve_validation(session, task_id, user)
        print(f"[green]Approved[/green] validation task {task.id[:8]}")
        print(
            f"  Segment [cyan]{task.segment_id[:8]}[/cyan] is now"
            " [bold green]validated[/bold green]"
        )


@app.command("reject")
def task_reject(
    task_id: str = typer.Argument(..., help="Validation task ID."),
    eaf_file: Path = typer.Argument(..., help="EAF with validator notes/corrections."),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
):
    """Reject a validation task. Creates a new annotation round."""
    cfg = get_config()
    factory = get_factory(cfg)
    storage = get_storage(cfg)
    user = whoami(cfg)

    with session_scope(factory) as session:
        val_task, new_task = task_pipeline.reject_validation(
            session, task_id, eaf_file, user, storage, notes=notes
        )
        print(f"[yellow]Rejected[/yellow] validation task {val_task.id[:8]}")
        print(
            f"[blue]Created[/blue] annotation task [cyan]{new_task.id[:8]}[/cyan] "
            f"(round {new_task.round}, base EAF = your annotated file)"
        )


@app.command("delete")
def task_delete(
    task_id: str = typer.Argument(..., help="Task ID to cancel/delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Cancel a task (marks it as cancelled, clears assignment and lock).

    Tasks are not hard-deleted to preserve audit history. Use --force to skip
    the confirmation prompt.
    """
    cfg = get_config()
    factory = get_factory(cfg)

    if not force:
        typer.confirm(f"Cancel task {task_id[:8]}?", abort=True)

    with session_scope(factory) as session:
        task = task_pipeline.cancel_task(session, task_id)
        print(f"[yellow]Cancelled[/yellow] task {task.id[:8]}")


@app.command("reannotate")
def task_reannotate(
    segment_id: str = typer.Argument(..., help="Segment ID to reannotate."),
    schema_version: Optional[str] = typer.Option(
        None, "--schema", "-s", help="Schema version (defaults to current)."
    ),
):
    """Trigger reannotation of a segment (e.g. after schema change)."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        new_task = task_pipeline.trigger_reannotation(
            session, segment_id, schema_version
        )
        print(
            f"[green]Reannotation triggered[/green] for segment {segment_id[:8]} — "
            f"new task [cyan]{new_task.id[:8]}[/cyan]"
        )
