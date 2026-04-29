"""`okapipy nlp ...` typer subcommands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel

from okapipy.cli.console import print_error, stderr
from okapipy.parser.errors import NlpModelMissingError
from okapipy.parser.nlp import DEFAULT_CACHE_DIR, fetch_model

app = typer.Typer(no_args_is_help=True, help="Manage local spaCy NLP models.")


@app.command("fetch")
def fetch(
    ctx: typer.Context,
    lang: str = typer.Argument(..., help="ISO language code, e.g. 'en'."),
    cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR,
        "--cache-dir",
        help="Directory in which to store the downloaded model.",
    ),
) -> None:
    """Download and install the spaCy model for `lang` into `cache_dir`."""
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    verbose = obj.get("verbose", 0)
    debug = isinstance(verbose, int) and verbose >= 2
    try:
        with stderr.status(f"Downloading spaCy model for '{lang}'", spinner="dots"):
            target = fetch_model(lang, cache_dir)
    except NlpModelMissingError as exc:
        print_error(exc, debug=debug)
        raise typer.Exit(code=1) from exc
    stderr.print(
        Panel(
            f"Installed model into {target}",
            border_style="green",
            title="NLP",
            title_align="left",
        )
    )
