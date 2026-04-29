"""`okapipy spec ...` typer subcommands."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer
from rich import box
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from okapipy.cli.console import is_piped, print_error, stderr, stdout, write_stream
from okapipy.parser.builder import build
from okapipy.parser.disambiguation import load_sidecar
from okapipy.parser.dump import to_json, write
from okapipy.parser.errors import ParserError
from okapipy.parser.loader import load_spec
from okapipy.parser.model import APIModel, Collection, Namespace, Resource
from okapipy.parser.nlp import DEFAULT_CACHE_DIR, load_pipeline

app = typer.Typer(no_args_is_help=True, help="Inspect and parse OpenAPI specifications.")


@app.command("parse")
def parse_command(
    ctx: typer.Context,
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
    verbose = _verbose_from(ctx)
    try:
        api = _run_pipeline(source=source, sidecar=sidecar, lang=lang, cache_dir=nlp_cache_dir)
    except ParserError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    _emit_summary(api, source=source)
    if output is not None:
        try:
            write(api, output)
        except ValueError as exc:
            print_error(exc, debug=verbose >= 2)
            raise typer.Exit(code=1) from exc
        stderr.print(Panel(f"Wrote {output}", border_style="green", title_align="left"))
        return
    _emit_json(api)


def _run_pipeline(
    *,
    source: str,
    sidecar: Path | None,
    lang: str,
    cache_dir: Path,
) -> APIModel:
    """Run the full parser pipeline with a spinner for each phase."""
    with _phase("Loading OpenAPI spec"):
        spec = load_spec(source)
    with _phase("Loading disambiguation sidecar"):
        side = load_sidecar(sidecar)
    with _phase(f"Loading spaCy pipeline ({lang})"):
        nlp = load_pipeline(lang, cache_dir=cache_dir)
    with _phase("Building structural tree"):
        return build(spec, side, nlp)


@contextmanager
def _phase(label: str) -> Iterator[None]:
    """Render a rich status spinner around a pipeline phase, on stderr."""
    with stderr.status(label, spinner="dots"):
        yield


def _emit_summary(api: APIModel, *, source: str) -> None:
    """Print a counts table for the parsed APIModel on stderr."""
    counts = _count_nodes(api)
    stderr.print(
        Panel(
            f"{source} parsing result",
            border_style="cyan",
            title_align="left",
        )
    )
    table = Table(
        box=box.ROUNDED,
        header_style="bold cyan",
        expand=True,
    )
    table.add_column("Node kind", style="cyan", no_wrap=True)
    table.add_column("Count", justify="right", style="bold")
    table.add_row("Namespaces", str(counts["namespaces"]))
    table.add_row("Collections", str(counts["collections"]))
    table.add_row("Resources", str(counts["resources"]))
    table.add_row("Actions", str(counts["actions"]))
    stderr.print(table)


def _count_nodes(api: APIModel) -> dict[str, int]:
    """Walk the APIModel tree and return counts per node kind."""
    counts = {"namespaces": 0, "collections": 0, "resources": 0, "actions": 0}
    for collection in api.collections:
        _count_collection(collection, counts)
    for namespace in api.namespaces:
        _count_namespace(namespace, counts)
    return counts


def _count_namespace(namespace: Namespace, counts: dict[str, int]) -> None:
    """Recurse into a Namespace, accumulating into `counts`."""
    counts["namespaces"] += 1
    for child in namespace.namespaces:
        _count_namespace(child, counts)
    for collection in namespace.collections:
        _count_collection(collection, counts)


def _count_collection(collection: Collection, counts: dict[str, int]) -> None:
    """Recurse into a Collection, accumulating into `counts`."""
    counts["collections"] += 1
    counts["actions"] += len(collection.actions)
    if collection.resource is not None:
        _count_resource(collection.resource, counts)


def _count_resource(resource: Resource, counts: dict[str, int]) -> None:
    """Recurse into a Resource, accumulating into `counts`."""
    counts["resources"] += 1
    counts["actions"] += len(resource.actions)
    for collection in resource.collections:
        _count_collection(collection, counts)


def _emit_json(api: APIModel) -> None:
    """Print JSON to stdout: syntax-highlighted on a TTY, plain when piped."""
    payload = to_json(api)
    if is_piped():
        write_stream(payload, file=sys.stdout)
        return
    stdout.print(Syntax(payload, "json", theme="ansi_dark", background_color="default"))


def _verbose_from(ctx: typer.Context) -> int:
    """Read the verbosity counter set by the top-level callback, defaulting to 0."""
    obj = ctx.obj if isinstance(ctx.obj, dict) else {}
    value = obj.get("verbose", 0)
    return int(value) if isinstance(value, int) else 0
