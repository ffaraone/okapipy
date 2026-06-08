"""`okapipy parse / generate / init` command implementations.

These functions are registered on the root `app` in `cli/__init__.py`;
they have Typer-friendly signatures but no `@app.command` decorator so
the registration site stays in one place. Naming convention:
`<command>_command` for the top-level command functions, underscore
prefix for the shared helpers.
"""

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

from okapipy.cli.console import (
    is_piped,
    print_error,
    stderr,
    stdout,
    warnings_emitted,
    write_stream,
)
from okapipy.generator import GenerationError, generate
from okapipy.generator.vfs import write_to_disk
from okapipy.manifest import (
    DEFAULT_MANIFEST_FILENAME,
    apply_cli_overrides,
    load_manifest,
)
from okapipy.parser.builder import build
from okapipy.parser.dump import to_json, write
from okapipy.parser.errors import ParserError
from okapipy.parser.inline_schemas import flatten_inline_schemas
from okapipy.parser.loader import load_spec
from okapipy.parser.model import APIModel, Collection, Namespace, Resource
from okapipy.parser.nlp import DEFAULT_CACHE_DIR, load_pipeline
from okapipy.parser.rules import load_rules


def parse_command(
    ctx: typer.Context,
    source: str = typer.Argument(
        ..., help="Path or http(s) URL of the OpenAPI document."
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
        help="Path prefix to strip from every path before classification "
        "(e.g. '/public/v1'); overrides the prefix inferred from servers[].url.",
    ),
    nlp_cache_dir: Path = typer.Option(
        DEFAULT_CACHE_DIR,
        "--nlp-cache-dir",
        help="Directory in which spaCy models are stored and looked up.",
    ),
    unmatched: str | None = typer.Option(
        None,
        "--unmatched",
        help=(
            "Top-level namespace to hold operations that would otherwise be "
            "dropped by the hierarchical routing table (e.g. PUT on a "
            "collection, GET on a bare namespace path). Each such op becomes "
            "a flat action named after its operationId. The name must not "
            "collide with an existing top-level node."
        ),
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
            unmatched_namespace=unmatched,
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
    unmatched_namespace: str | None = None,
) -> APIModel:
    """Run the full parser pipeline with a spinner for each phase."""
    with _phase("Loading OpenAPI spec"):
        spec = load_spec(source)
        spec = flatten_inline_schemas(spec)
    with _phase("Loading rules"):
        loaded_rules = load_rules(rules)
    with _phase(f"Loading spaCy pipeline ({lang})"):
        nlp = load_pipeline(lang, cache_dir=cache_dir)
    with _phase("Building structural tree"):
        return build(
            spec,
            loaded_rules,
            nlp,
            strip_prefix=strip_prefix,
            unmatched_namespace=unmatched_namespace,
        )


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


def generate_command(
    ctx: typer.Context,
    manifest_path: Path = typer.Option(
        Path(DEFAULT_MANIFEST_FILENAME),
        "--manifest",
        help=(
            "Path to the project manifest. Defaults to ./okapipy.yml. "
            "All project-level and per-spec configuration lives in this file."
        ),
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help=(
            "Directory to write the generated client project into. Overrides "
            "the manifest's `output` field on conflict; required when the "
            "manifest omits `output`."
        ),
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
    """Generate a Python client project from the project manifest (okapipy.yml)."""
    verbose = _verbose_from(ctx)
    try:
        with _phase(f"Loading manifest {manifest_path}"):
            manifest = load_manifest(manifest_path)
        manifest = apply_cli_overrides(manifest, output=output)
        if manifest.output is None:
            print_error(
                ValueError(
                    "no output directory: pass --output PATH or add `output: …` "
                    "to the manifest."
                ),
                debug=verbose >= 2,
            )
            raise typer.Exit(code=1)
        output_dir = manifest.output
    except ParserError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    except GenerationError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    try:
        with _phase("Parsing spec(s) and generating client project"):
            vfs = generate(manifest)
        action = "Checking" if check else f"Writing {len(vfs)} files to {output_dir}"
        with _phase(action):
            report = write_to_disk(vfs, output_dir, dry_run=check)
    except ParserError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    except GenerationError as exc:
        print_error(exc, debug=verbose >= 2)
        raise typer.Exit(code=1) from exc
    if not quiet:
        for warning in report.warnings:
            stderr.print(
                Panel(
                    warning, border_style="yellow", title="WARNING", title_align="left"
                )
            )
    warning_count = warnings_emitted()
    warning_tail = _warning_tail(warning_count)
    if check:
        if report.would_change or report.warnings:
            stderr.print(
                Panel(
                    _check_summary(report) + warning_tail,
                    border_style="red",
                    title="--check failed",
                    title_align="left",
                )
            )
            raise typer.Exit(code=1)
        stderr.print(
            Panel(
                "No changes; no drift." + warning_tail,
                border_style="green",
                title="--check passed",
                title_align="left",
            )
        )
        return
    summary = f"Wrote {len(report.written)} files to {output_dir}"
    if report.skipped:
        summary += f"; skipped {len(report.skipped)} existing user-layer files"
    if report.pruned:
        summary += f"; pruned {len(report.pruned)} stale base files"
    summary += warning_tail
    stderr.print(Panel(summary, border_style="green", title_align="left"))


def init_command(
    ctx: typer.Context,
    source: str | None = typer.Argument(
        None,
        help=(
            "Optional path or URL of an OpenAPI document. When given, the "
            "scaffold contains one `specs[]` entry pointing at it (mounted "
            "at the root); when omitted, the scaffold's `specs:` array is "
            "empty and the user fills it in."
        ),
    ),
    manifest_path: Path = typer.Option(
        Path(DEFAULT_MANIFEST_FILENAME),
        "--manifest",
        help="Where to write the starter manifest. Defaults to ./okapipy.yml.",
    ),
    package: str | None = typer.Option(
        None,
        "--package",
        help=(
            "Dotted Python package path. Written verbatim into the manifest; "
            "omit to leave a TODO placeholder."
        ),
    ),
    client_class: str | None = typer.Option(
        None,
        "--client-class",
        help=(
            "PascalCase client class name. Written verbatim into the manifest; "
            "omit to leave a TODO placeholder."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite the manifest file if it already exists.",
    ),
) -> None:
    """Scaffold a starter okapipy.yml at --manifest (default ./okapipy.yml)."""
    verbose = _verbose_from(ctx)
    if manifest_path.exists() and not force:
        print_error(
            FileExistsError(
                f"refusing to overwrite existing manifest at {manifest_path}; "
                "pass --force to replace it."
            ),
            debug=verbose >= 2,
        )
        raise typer.Exit(code=1)
    body = _starter_manifest_body(
        source=source,
        package=package,
        client_class=client_class,
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(body, encoding="utf-8")
    stderr.print(
        Panel(
            f"Wrote starter manifest to {manifest_path}",
            border_style="green",
            title_align="left",
        )
    )


def _starter_manifest_body(
    *,
    source: str | None,
    package: str | None,
    client_class: str | None,
) -> str:
    """Render the YAML body of a starter okapipy.yml.

    The starter populates every PEP 621-flavored project metadata field
    with a lowercase `acme.commerce`-shaped example so a fresh user sees
    the full surface and can edit in place. When `source` is omitted,
    `specs:` is left empty so the manifest fails validation (`specs[]`
    is required) until the user fills in real targets — the safety net
    against accidentally generating against placeholder values.
    """
    pkg = package or "acme.commerce"
    cls = client_class or "CommerceClient"
    project_name = pkg.rsplit(".", 1)[-1]
    if source is None:
        specs_block = (
            "specs:\n"
            "  # Each entry mounts one OpenAPI spec under a namespace.\n"
            "  # Use `namespace: ''` to mount the spec at the root.\n"
            "  # - namespace: ''\n"
            "  #   source: ./openapi.yaml\n"
            "  #   rules: ./rules.yaml  # optional, local path only\n"
        )
    else:
        specs_block = f"specs:\n  - namespace: ''\n    source: {source}\n"
    return (
        "# okapipy project manifest. See https://ffaraone.github.io/okapipy/ for details.\n"
        "\n"
        "# Required.\n"
        f"package: {pkg}\n"
        f"client_class: {cls}\n"
        "\n"
        "# Project metadata — drives pyproject.toml, LICENSE, and README.\n"
        f"project_name: {project_name}\n"
        f"project_description: Generated client for {project_name}\n"
        'project_version: "0.1.0"\n'
        'python_version: "3.13"\n'
        "license: Proprietary  # SPDX id (MIT, Apache-2.0, BSD-3-Clause, MPL-2.0, ...)\n"
        "author: Your Organization\n"
        "repo_url: https://github.com/your-org/your-repo\n"
        "\n"
        "# Optional generation settings — see the manifest reference for details.\n"
        "# shape: auto  # auto | models | dicts\n"
        "# output: ./out\n"
        "\n"
        f"{specs_block}"
    )


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


def _warning_tail(count: int) -> str:
    """Return `"; N warning(s) emitted"` when `count > 0`, otherwise an empty string.

    Used to append the run-wide warning tally to the final summary panel so the
    user notices parser-level skips (`skipping path …`) that scrolled off-screen
    above the panel.
    """
    if count <= 0:
        return ""
    suffix = "" if count == 1 else "s"
    return f"; {count} warning{suffix} emitted"
