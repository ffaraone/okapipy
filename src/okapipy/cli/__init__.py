"""Typer-based CLI surface for okapipy."""

from __future__ import annotations

import typer

from okapipy.cli import nlp_cmd, spec_cmd
from okapipy.cli.console import setup_logging

app = typer.Typer(no_args_is_help=True, help="okapipy — Python OpenAPI client generator.")
app.add_typer(nlp_cmd.app, name="nlp")
app.add_typer(spec_cmd.app, name="spec")


@app.callback()
def main(
    ctx: typer.Context,
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        help="Increase verbosity: -v enables INFO logs, -vv enables DEBUG logs.",
    ),
) -> None:
    """Entry-point callback that initializes shared CLI state for every subcommand."""
    setup_logging(verbose)
    ctx.obj = {"verbose": verbose}
