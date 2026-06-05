"""Typer-based CLI surface for okapipy.

Four top-level commands are exposed: `init`, `generate`, `parse`, and
`fetch-language`. The command functions live in `cli.spec_cmd` (the
three spec-related ones) and `cli.nlp_cmd` (the model fetcher); this
module wires them into one flat root app.
"""

from __future__ import annotations

import typer

from okapipy.cli.console import setup_logging
from okapipy.cli.nlp_cmd import fetch_language_command
from okapipy.cli.spec_cmd import (
    generate_command,
    init_command,
    parse_command,
)

app = typer.Typer(
    no_args_is_help=True,
    help="okapipy — Python OpenAPI client generator.",
)
app.command("init")(init_command)
app.command("generate")(generate_command)
app.command("parse")(parse_command)
app.command("fetch-language")(fetch_language_command)


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
