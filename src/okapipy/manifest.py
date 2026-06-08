"""Project manifest: the user-authored input that drives `okapipy generate`.

The manifest is a small YAML (or JSON) document checked into the consumer's
repository and is the *only* input the `generate` command needs at the
command line — every project-level option (package, client class, response
shape, template directories) and every per-spec option (source, rules,
strip-prefix, unmatched, language) lives inside it. The CLI default
location is `./okapipy.yml`.

A manifest carries one or more `specs[]` entries. Each entry pairs an
OpenAPI spec source with the mount namespace under which its parser tree
is composed into the generated client; an empty namespace mounts the
spec at the root (the historical single-spec behavior). The generator
parses every entry independently and then composes the results — multi-
spec composition is *not* a parser concern.

This module is intentionally narrow: it owns the Pydantic models and the
loader/validator. The composition logic that turns a list of parsed
APIModels into a single generated client lives in
`okapipy.generator.compose`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from okapipy.generator.errors import ManifestFormatError, ManifestNotFoundError

DEFAULT_MANIFEST_FILENAME = "okapipy.yml"
"""Default filename the CLI looks for when `--manifest` is not given."""

_LOCAL_URL_SCHEMES = frozenset({"http", "https"})
"""URL schemes that are *not* accepted in the `rules` field (local paths only)."""

Shape = Literal["auto", "models", "dicts"]
"""Response-shape policy for the generated client.

Mirrors the `shape` parameter of `okapipy.generator.generate` — see that
function for the per-mode semantics. The choice is project-wide; it cannot
vary per spec entry, because a single generated client has one type
surface.
"""


class SpecEntry(BaseModel):
    """One OpenAPI spec mounted under a namespace in the generated client.

    Each entry is parsed independently with its own per-spec settings
    (`rules`, `strip_prefix`, `unmatched`, `lang`). The resulting APIModel
    is composed into the project under `namespace`; `""` mounts at the
    root (single-spec, historical layout) while a dotted string nests
    under intermediate namespaces (`platform.users` shares the `platform`
    parent with `platform.billing`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: Annotated[
        str,
        Field(
            description=(
                "Dotted mount path for this spec's tree (`''` mounts at the "
                "root). Intermediate segments are synthesized as `Namespace` "
                "nodes and may be shared by multiple specs."
            ),
        ),
    ]
    source: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Path or `http(s)://` URL of the OpenAPI document. Resolved "
                "relative to the manifest file's parent directory."
            ),
        ),
    ]
    rules: Annotated[
        Path | None,
        Field(
            default=None,
            description=(
                "Local path to a JSON/YAML rules file (URLs rejected). "
                "Resolved relative to the manifest file's parent directory."
            ),
        ),
    ] = None
    strip_prefix: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Path prefix to strip from every operation in this spec before "
                "classification. Overrides the prefix inferred from "
                "`servers[].url`."
            ),
        ),
    ] = None
    unmatched: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "Top-level namespace name (inside this spec's mount) to hold "
                "operations the routing table would otherwise drop. The same "
                "semantics as the historical `--unmatched` CLI flag."
            ),
        ),
    ] = None
    lang: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "ISO language code for the NLP pipeline. When unset, inherits "
                "the manifest's top-level `lang`."
            ),
        ),
    ] = None

    @field_validator("rules", mode="before")
    @classmethod
    def _reject_url_rules(cls, value: object) -> object:
        """`rules` accepts a local path only — URLs are rejected at load time.

        Inspect the raw string *before* Pydantic coerces it into a Path
        (which silently collapses `//` and would hide the URL shape).
        """
        if isinstance(value, str) and "://" in value:
            scheme = value.split("://", 1)[0].lower()
            if scheme in _LOCAL_URL_SCHEMES:
                raise ValueError(f"rules must be a local path, got URL: {value}")
        return value


class GenerationManifest(BaseModel):
    """Project-wide configuration for `okapipy generate`.

    Carries the project-level fields that drive one generated Python
    package (`package`, `client_class`, `shape`, …) plus the `specs[]`
    array describing what trees live inside it. The default file location
    is `./okapipy.yml`; `load_manifest(path)` reads it from disk.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    package: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Dotted Python package path for the generated client "
                "(e.g. `acme.commerce`)."
            ),
        ),
    ]
    client_class: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "PascalCase class name for the sync client; the async sibling "
                "is `Async<client_class>`."
            ),
        ),
    ]
    specs: Annotated[
        list[SpecEntry],
        Field(
            min_length=1,
            description="One or more spec entries; at least one is required.",
        ),
    ]

    project_name: str | None = None
    project_description: str | None = None
    project_version: str = "0.1.0"
    python_version: str = "3.13"
    license: str = "Proprietary"
    author: str | None = None
    repo_url: str | None = None

    shape: Shape = "auto"
    lang: str = "en"
    nlp_cache_dir: Path | None = None
    templates_dir: Path | None = None
    model_templates_dir: Path | None = None

    output: Path | None = None

    @model_validator(mode="after")
    def _check_mount_collisions(self) -> GenerationManifest:
        """Reject manifests where two `specs[]` entries share a mount path.

        A mount path is the dotted `namespace` split on `.`. Two specs
        resolving to the same tuple cannot coexist because the generator
        would try to emit the same subtree twice.
        """
        seen: dict[tuple[str, ...], int] = {}
        for index, entry in enumerate(self.specs):
            key = _split_mount(entry.namespace)
            if key in seen:
                first = seen[key]
                label = ".".join(key) if key else "<root>"
                raise ValueError(
                    f"mount namespace collision: specs[{first}] and "
                    f"specs[{index}] both mount at {label!r}"
                )
            seen[key] = index
        return self


def load_manifest(path: Path) -> GenerationManifest:
    """Read and validate the project manifest at `path`.

    The file extension drives format selection: `.json` is parsed as JSON,
    `.yml` / `.yaml` (and anything else) as YAML. Relative path fields on
    the manifest (`source`, `rules`, `templates_dir`, `model_templates_dir`,
    `nlp_cache_dir`, `output`) are resolved against the manifest file's
    parent directory before the model is returned, so the manifest is
    portable with its consumer repository.

    Raises:
        ManifestNotFoundError: when the file does not exist on disk.
        ManifestFormatError: when the file is malformed JSON/YAML or
            when the document fails schema validation (missing required
            fields, mount-namespace collisions, URL `rules` entries, …).

    Returns:
        A populated, frozen `GenerationManifest`. Path fields are absolute.
    """
    if not path.exists():
        raise ManifestNotFoundError(f"manifest not found: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestNotFoundError(f"cannot read manifest at {path}: {exc}") from exc
    try:
        data = _decode(path, raw_text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ManifestFormatError(
            f"manifest {path} is not valid JSON/YAML: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ManifestFormatError(
            f"manifest {path} must be a mapping at the top level, got {type(data).__name__}"
        )
    try:
        manifest = GenerationManifest.model_validate(data)
    except ValueError as exc:
        raise ManifestFormatError(f"manifest {path} failed validation: {exc}") from exc
    return _resolve_relative_paths(manifest, base=path.parent.resolve())


def apply_cli_overrides(
    manifest: GenerationManifest, *, output: Path | None = None
) -> GenerationManifest:
    """Return a copy of `manifest` with selected fields replaced by CLI overrides.

    Only the narrow set of fields that have a CLI counterpart is honored;
    every other manifest field requires editing the manifest itself. Today
    that set is just `output`.
    """
    overrides: dict[str, Path] = {}
    if output is not None:
        overrides["output"] = output.resolve()
    if not overrides:
        return manifest
    return manifest.model_copy(update=overrides)


def _decode(path: Path, text: str) -> object:
    """Decode the manifest text using the suffix-derived format."""
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def _split_mount(namespace: str) -> tuple[str, ...]:
    """Split a dotted mount path; the empty string maps to the empty tuple."""
    cleaned = namespace.strip().strip(".")
    if not cleaned:
        return ()
    return tuple(cleaned.split("."))


def _resolve_relative_paths(
    manifest: GenerationManifest, *, base: Path
) -> GenerationManifest:
    """Re-anchor every path field in `manifest` against `base`.

    Pydantic gives us back paths that mirror the user's string verbatim; we
    re-anchor them against the manifest's parent directory so callers don't
    have to remember to do it. URL strings (in `source`) are passed through
    untouched.
    """
    spec_updates: list[SpecEntry] = []
    for entry in manifest.specs:
        spec_updates.append(
            entry.model_copy(
                update={
                    "source": _anchor_source(entry.source, base),
                    "rules": _anchor_path(entry.rules, base),
                }
            )
        )
    return manifest.model_copy(
        update={
            "specs": spec_updates,
            "nlp_cache_dir": _anchor_path(manifest.nlp_cache_dir, base),
            "templates_dir": _anchor_path(manifest.templates_dir, base),
            "model_templates_dir": _anchor_path(manifest.model_templates_dir, base),
            "output": _anchor_path(manifest.output, base),
        }
    )


def _anchor_source(source: str, base: Path) -> str:
    """Resolve a `SpecEntry.source` against `base`, leaving URLs untouched."""
    if "://" in source:
        scheme = source.split("://", 1)[0].lower()
        if scheme in _LOCAL_URL_SCHEMES:
            return source
    candidate = Path(source)
    if candidate.is_absolute():
        return str(candidate)
    return str((base / candidate).resolve())


def _anchor_path(value: Path | None, base: Path) -> Path | None:
    """Resolve `value` against `base`; pass through `None` and absolute paths."""
    if value is None:
        return None
    if value.is_absolute():
        return value
    return (base / value).resolve()
