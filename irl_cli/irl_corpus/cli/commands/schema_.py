"""CLI commands: irl schema ..."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print  # pylint: disable=redefined-builtin
from rich import box
from rich.table import Table

from irl_corpus.cli.ctx import get_config, get_factory
from irl_corpus.db import session_scope
from irl_corpus.db.queries import list_schemas
from irl_corpus.pipeline.schema import import_schema, set_current

app = typer.Typer(help="Manage annotation schemas.", no_args_is_help=True)


@app.command("import")
def schema_import(
    json_file: Path = typer.Argument(..., help="Path to annotation schema JSON."),
    set_as_current: bool = typer.Option(
        True,
        "--current/--no-current",
        help="Set this schema as the active one after import.",
    ),
):
    """Import an annotation schema JSON file."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        schema = import_schema(session, json_file)
        print(
            f"[green]Imported[/green] schema "
            f"[bold]{schema.version}[/bold] ({schema.date})"
            f"  id={schema.id[:8]}"
        )
        if set_as_current:
            set_current(session, schema.id)
            print("  Set as [bold green]current[/bold green]")


@app.command("list")
def schema_list():
    """List all imported annotation schemas."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        schemas = list_schemas(session)

    if not schemas:
        print("[dim]No schemas imported yet.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Version")
    table.add_column("Date")
    table.add_column("Current")
    table.add_column("Imported")

    for schema in schemas:
        table.add_row(
            schema.id[:8],
            schema.version,
            schema.date,
            "[green]✓[/green]" if schema.is_current else "",
            schema.imported_at.strftime("%Y-%m-%d"),
        )
    print(table)


@app.command("set-current")
def schema_set_current(
    schema_id: str = typer.Argument(..., help="Schema ID or version string."),
):
    """Set a schema as the active/current one."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        schema = set_current(session, schema_id)
        print(
            f"[green]Set current schema:[/green] "
            f"[bold]{schema.version}[/bold] ({schema.id[:8]})"
        )
