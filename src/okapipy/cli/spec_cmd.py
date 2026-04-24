"""`okapipy spec ...` typer subcommands."""

from __future__ import annotations

from pathlib import Path

import typer

from okapipy.parser.api import parse
from okapipy.parser.dump import to_json, write
from okapipy.parser.errors import ParserError
from okapipy.parser.nlp import DEFAULT_CACHE_DIR

app = typer.Typer(no_args_is_help=True, help="Inspect and parse OpenAPI specifications.")


@app.command("parse")
def parse_command(
    source: str = typer.Argument(..., help="Path or http(s) URL of the OpenAPI document."),
    sidecar: Path | None = typer.Option(
        None,
        "--sidecar",
        help="Local path to a JSON/YAML disambiguation sidecar.",
    ),
    lang: str = typer.Option("en", "--lang", help="ISO language code for NLP."),
    nlp_cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR,
        "--nlp-cache-dir",
        help="Directory in which spaCy models are stored and looked up.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Optional output path; format is inferred from .json/.yaml/.yml extension.",
    ),
) -> None:
    """Parse an OpenAPI document and print or save the resulting structural tree."""
    try:
        api = parse(
            source,
            sidecar=sidecar,
            lang=lang,
            nlp_cache_dir=nlp_cache_dir,
        )
    except ParserError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if output is None:
        typer.echo(to_json(api))
        return
    try:
        write(api, output)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {output}")
