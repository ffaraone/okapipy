"""`okapipy nlp ...` typer subcommands."""

from __future__ import annotations

from pathlib import Path

import typer

from okapipy.parser.errors import NlpModelMissingError
from okapipy.parser.nlp import DEFAULT_CACHE_DIR, fetch_model

app = typer.Typer(no_args_is_help=True, help="Manage local spaCy NLP models.")


@app.command("fetch")
def fetch(
    lang: str = typer.Argument(..., help="ISO language code, e.g. 'en'."),
    cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR,
        "--cache-dir",
        help="Directory in which to store the downloaded model.",
    ),
) -> None:
    """Download and install the spaCy model for `lang` into `cache_dir`."""
    try:
        target = fetch_model(lang, cache_dir)
    except NlpModelMissingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Installed model into {target}")
