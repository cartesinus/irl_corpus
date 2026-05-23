"""CLI commands: irl user ..."""
from __future__ import annotations

import typer
from rich import print  # pylint: disable=redefined-builtin
from rich import box
from rich.table import Table

from irl_corpus.cli.ctx import get_config, get_factory
from irl_corpus.db import session_scope
from irl_corpus.db.models import User
from irl_corpus.db.queries import get_user, list_users

app = typer.Typer(help="Manage annotators.", no_args_is_help=True)


@app.command("add")
def user_add(
    name: str = typer.Option(..., "--name", "-n", help="Unique username."),
    role: str = typer.Option(
        "annotator", "--role", "-r", help="Role: admin | annotator."
    ),
):
    """Add a new user."""
    cfg = get_config()
    factory = get_factory(cfg)

    if role not in ("admin", "annotator"):
        typer.echo(f"Invalid role '{role}'. Must be 'admin' or 'annotator'.", err=True)
        raise typer.Exit(1)

    with session_scope(factory) as session:
        existing = get_user(session, name)
        if existing:
            print(
                f"[yellow]User '{name}' already exists[/yellow]"
                f" (id={existing.id[:8]}, role={existing.role})"
            )
            return
        user = User(name=name, role=role)
        session.add(user)
        session.flush()
        print(
            f"[green]Added[/green] user [bold]{name}[/bold]"
            f" (role={role}, id={user.id[:8]})"
        )


@app.command("list")
def user_list():
    """List all users."""
    cfg = get_config()
    factory = get_factory(cfg)

    with session_scope(factory) as session:
        users = list_users(session)

    if not users:
        print("[dim]No users found.[/dim]")
        return

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Created")

    for user in users:
        table.add_row(
            user.id[:8],
            user.name,
            user.role,
            user.created_at.strftime("%Y-%m-%d"),
        )
    print(table)
