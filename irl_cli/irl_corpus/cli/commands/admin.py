"""CLI commands: irl admin ..."""
from __future__ import annotations

import typer
from rich import print  # pylint: disable=redefined-builtin
from rich import box
from rich.table import Table

from irl_corpus.cli.ctx import get_config, get_factory
from irl_corpus.db import init_db, session_scope
from irl_corpus.db.models import Segment, Task, User
from irl_corpus.db.queries import (
    list_recordings,
    list_schemas,
    list_tasks,
    list_users,
    require_task,
)
from irl_corpus.pipeline.tasks import release_working

app = typer.Typer(help="Admin operations.", no_args_is_help=True)


@app.command("init")
def admin_init():
    """Initialise (or migrate) the database schema."""
    cfg = get_config()
    print(f"Initialising database: [cyan]{cfg.database.url}[/cyan]")
    init_db(cfg.database.url)
    print("[green]Database ready.[/green]")


@app.command("stats")
def admin_stats():
    """Show corpus-wide annotation progress."""
    # pylint: disable=too-many-locals
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        recordings = list_recordings(session)
        total_recs = len(recordings)

        segments = session.query(Segment).all()
        content_segs = [
            seg for seg in segments if seg.type == "content" and seg.status == "active"
        ]
        gap_segs = [
            seg for seg in segments if seg.type == "gap" and seg.status == "active"
        ]
        removed_segs = [seg for seg in segments if seg.status == "removed"]

        tasks = list_tasks(session)
        ann_tasks = [task for task in tasks if task.type == "annotation"]
        val_tasks = [task for task in tasks if task.type == "validation"]

        # annotation status counts
        status_counts: dict[str, int] = {}
        for seg in content_segs:
            status_counts[seg.annotation_status] = (
                status_counts.get(seg.annotation_status, 0) + 1
            )

        validated = status_counts.get("validated", 0)
        total_content = len(content_segs)

        schemas = list_schemas(session)
        current_schema = next((sch for sch in schemas if sch.is_current), None)
        users = list_users(session)

    print()
    print("[bold]Corpus Statistics[/bold]")
    print("─" * 40)

    print(f"  Recordings:   {total_recs}")
    print(
        f"  Schemas:      {len(schemas)}"
        + (
            f"  (current: [bold]{current_schema.version}[/bold])"
            if current_schema
            else ""
        )
    )
    print(f"  Users:        {len(users)}")
    print()

    print("  Segments:")
    print(f"    content:    {len(content_segs)}")
    print(f"    gaps:       {len(gap_segs)}")
    print(f"    removed:    {len(removed_segs)}")
    print()

    print("  Tasks:")
    print(f"    annotation: {len(ann_tasks)}")
    print(f"    validation: {len(val_tasks)}")
    print()

    print("  Annotation status (content segments):")
    for status in (
        "pending",
        "in_annotation",
        "in_validation",
        "needs_rework",
        "validated",
    ):
        count = status_counts.get(status, 0)
        pct = count / total_content * 100 if total_content else 0
        status_bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        style = {
            "validated": "green",
            "in_validation": "blue",
            "in_annotation": "yellow",
            "needs_rework": "red",
            "pending": "dim",
        }.get(status, "")
        print(
            f"    [{style}]{status:16s}[/{style}]  {status_bar}  {count:4d}  ({pct:.1f}%)"
        )

    print()
    if total_content:
        pct_done = validated / total_content * 100
        bar_len = int(pct_done / 5)
        overall_bar = (
            "[green]"
            + "█" * bar_len
            + "[/green][dim]"
            + "░" * (20 - bar_len)
            + "[/dim]"
        )
        print(
            f"  [bold]Overall validated:[/bold]  {overall_bar}"
            f"  {validated}/{total_content}  ({pct_done:.1f}%)"
        )
    print()


def _resolve_user_name(session, user_id: str | None) -> str:
    """Look up a user name by ID, returning '-' if not found."""
    if not user_id:
        return "-"
    user = session.get(User, user_id)
    return user.name if user else user_id[:8]


@app.command("locks")
def admin_locks(
    task_id: str = typer.Argument(None, help="Show lock info for a specific task."),
):
    """Show which tasks currently have a working-on soft lock."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        if task_id:
            tasks = [require_task(session, task_id)]
        else:
            tasks = session.query(Task).filter(Task.working_on.isnot(None)).all()

        if not tasks:
            print("[dim]No active locks.[/dim]")
            return

        table = Table(box=box.SIMPLE_HEAD)
        table.add_column("Task ID", style="cyan")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Segment")
        table.add_column("Assigned to")
        table.add_column("Working on")
        table.add_column("Updated")

        for task in tasks:
            table.add_row(
                task.id[:8],
                task.type,
                task.status,
                task.segment_id[:8],
                _resolve_user_name(session, task.assigned_to),
                _resolve_user_name(session, task.working_on),
                task.updated_at.strftime("%Y-%m-%d %H:%M"),
            )
        print(table)


@app.command("lock-clear")
def admin_lock_clear(
    task_id: str = typer.Argument(..., help="Task ID to clear the lock on."),
):
    """Force-clear the working-on lock on a task."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        task = release_working(session, task_id)
        print(f"[yellow]Cleared[/yellow] working lock on task {task.id[:8]}")
