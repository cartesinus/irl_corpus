"""IRL Corpus annotation pipeline CLI."""

from __future__ import annotations

import typer

from irl_corpus.cli.commands import (
    admin,
    annotate,
    recording,
    schema_,
    segment,
    task,
    user,
)

app = typer.Typer(
    name="irl",
    help="IRL corpus annotation pipeline.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

app.add_typer(recording.app, name="recording")
app.add_typer(segment.app, name="segment")
app.add_typer(task.app, name="task")
app.add_typer(schema_.app, name="schema")
app.add_typer(user.app, name="user")
app.add_typer(admin.app, name="admin")
app.add_typer(annotate.app, name="annotate")

if __name__ == "__main__":
    app()
