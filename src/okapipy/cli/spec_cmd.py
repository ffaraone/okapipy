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
from okapipy.generator import GenerationError, generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.builder import build
from okapipy.parser.dump import to_json, write
from okapipy.parser.errors import ParserError
from okapipy.parser.loader import load_spec
from okapipy.parser.model import APIModel, Collection, Namespace, Resource
from okapipy.parser.nlp import DEFAULT_CACHE_DIR, load_pipeline
from okapipy.parser.rules import load_rules

app = typer.Typer(no_args_is_help=True, help="Inspect and parse OpenAPI specifications.")


@app.command("parse")
def parse_command(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Path or http(s) URL of the OpenAPI document."),
    rules: Path | None = typer.Option(
        None,
        "--rules",
        help="Local path to a JSON/YAML rules file.",
    ),
    lang: str = typer.Option("en", "--lang", help="ISO language code for NLP."),
    strip_prefix: str | None = typer.Option(
        None,
        "--strip-prefix",
        help="Path prefix to strip from every path before classification "
        "(e.g. '/public/v1'); overrides the prefix inferred from servers[].url.",
    ),
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
        api = _run_pipeline(
            source=source,
            rules=rules,
            lang=lang,
            cache_dir=nlp_cache_dir,
            strip_prefix=strip_prefix,
        )
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
    rules: Path | None,
    lang: str,
    cache_dir: Path,
    strip_prefix: str | None,
) -> APIModel:
    """Run the full parser pipeline with a spinner for each phase."""
    with _phase("Loading OpenAPI spec"):
        spec = load_spec(source)
    with _phase("Loading rules"):
        loaded_rules = load_rules(rules)
    with _phase(f"Loading spaCy pipeline ({lang})"):
        nlp = load_pipeline(lang, cache_dir=cache_dir)
    with _phase("Building structural tree"):
        return build(spec, loaded_rules, nlp, strip_prefix=strip_prefix)


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


@app.command("generate")
def generate_command(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Path or http(s) URL of the OpenAPI document."),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="Directory to write the generated client project into.",
    ),
    package: str = typer.Option(
        ...,
        "--package",
        help="Dotted Python package path for the generated client (e.g. 'acme.commerce').",
    ),
    client_class: str = typer.Option(
        ...,
        "--client-class",
        help="PascalCase class name for the sync client; async sibling is 'Async<name>'.",
    ),
    project_name: str | None = typer.Option(
        None,
        "--project-name",
        help="PEP 503 distribution name; defaults to the last segment of --package.",
    ),
    project_version: str = typer.Option(
        "0.1.0",
        "--project-version",
        help="Initial version string emitted into pyproject.toml.",
    ),
    python_version: str = typer.Option(
        "3.13",
        "--python-version",
        help="Pinned Python version for the generated project.",
    ),
    license_id: str = typer.Option(
        "Proprietary",
        "--license",
        help="SPDX license identifier; drives the LICENSE placeholder.",
    ),
    rules: Path | None = typer.Option(
        None,
        "--rules",
        help="Local path to a JSON/YAML rules file.",
    ),
    lang: str = typer.Option("en", "--lang", help="ISO language code for NLP."),
    strip_prefix: str | None = typer.Option(
        None,
        "--strip-prefix",
        help="Path prefix to strip from every path before classification.",
    ),
    nlp_cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR,
        "--nlp-cache-dir",
        help="Directory in which spaCy models are stored and looked up.",
    ),
    templates_dir: Path | None = typer.Option(
        None,
        "--templates-dir",
        help="Directory of user Jinja templates that override the packaged defaults.",
    ),
    model_templates_dir: Path | None = typer.Option(
        None,
        "--model-templates-dir",
        help="Directory of datamodel-code-generator templates for models.py.",
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help=(
            "Dry-run: report what would change but do not write or delete. "
            "Exits non-zero when any base file differs, any drift warning "
            "fires, or any stale base file would be pruned. CI gate."
        ),
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress drift-detection warnings. Pruning still runs.",
    ),
) -> None:
    """Generate a Python client project from an OpenAPI document."""
    verbose = _verbose_from(ctx)
    try:
        api = _run_pipeline(
            source=source,
            rules=rules,
            lang=lang,
            cache_dir=nlp_cache_dir,
            strip_prefix=strip_prefix,
        )
    except ParserError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    try:
        with _phase("Generating client project"):
            vfs = generate(
                api,
                source,
                output_dir=output,
                package=package,
                client_class=client_class,
                project_name=project_name,
                project_version=project_version,
                python_version=python_version,
                license=license_id,
                templates_dir=templates_dir,
                model_templates_dir=model_templates_dir,
            )
        action = "Checking" if check else f"Writing {len(vfs)} files to {output}"
        with _phase(action):
            report = write_to_disk(vfs, output, dry_run=check)
    except GenerationError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    if not quiet:
        for warning in report.warnings:
            stderr.print(Panel(warning, border_style="yellow", title="WARNING", title_align="left"))
    if check:
        if report.would_change or report.warnings:
            stderr.print(
                Panel(
                    _check_summary(report),
                    border_style="red",
                    title="--check failed",
                    title_align="left",
                )
            )
            raise typer.Exit(code=1)
        stderr.print(
            Panel(
                "No changes; no drift.",
                border_style="green",
                title="--check passed",
                title_align="left",
            )
        )
        return
    summary = f"Wrote {len(report.written)} files to {output}"
    if report.skipped:
        summary += f"; skipped {len(report.skipped)} existing user-layer files"
    if report.pruned:
        summary += f"; pruned {len(report.pruned)} stale base files"
    stderr.print(Panel(summary, border_style="green", title_align="left"))


def _check_summary(report) -> str:  # type: ignore[no-untyped-def]
    """One-line summary of what `--check` found."""
    parts: list[str] = []
    if report.would_change:
        parts.append("base content would change")
    if report.warnings:
        parts.append(f"{len(report.warnings)} drift warning(s)")
    if report.pruned:
        parts.append(f"{len(report.pruned)} stale base file(s) would be pruned")
    return "; ".join(parts) or "no changes"
