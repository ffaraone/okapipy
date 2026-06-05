"""Multi-spec composition: tag each parsed APIModel with its manifest mount path.

The parser is single-spec by design; this module turns a list of parsed
trees plus their manifest mount declarations into the small data structure
the generator's emit pipeline consumes. A ``MountedSpec`` is the unit the
emit loop iterates over — one per ``specs[]`` entry in the project
manifest, with the per-spec inputs (mount path, parsed APIModel, raw
spec for ``datamodel-code-generator``) bundled together so downstream
code does not have to thread separate lists.

The mount path is a tuple of segments (`("users",)` for ``namespace:
users``; `("platform", "users")` for ``namespace: platform.users``; `()`
for the root mount declared with ``namespace: ""``). The same tuple
drives:

* the on-disk sub-directory under ``base/`` and the user layer
  (``base/platform/users/...``),
* the synthetic mount-namespace tree the client class wires its accessors
  through, and
* the cross-mount-collision check that defends the emit pipeline against
  any manifest that snuck past the loader.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from okapipy.generator.errors import GenerationError
from okapipy.manifest import GenerationManifest
from okapipy.parser.api import parse as parse_spec
from okapipy.parser.model import APIModel, Namespace
from okapipy.parser.source_context import source_context


@dataclass(frozen=True)
class MountedSpec:
    """One spec entry's parser output tagged with its mount path.

    `mount_path` is the canonical tuple form (`()` for the root mount). The
    raw spec is preserved alongside the parsed `APIModel` because
    `datamodel-code-generator` consumes the *raw* document — not the
    parser's tree — and the emit pipeline calls it once per mount.

    `spec_index` records the entry's original position in the manifest's
    `specs[]` array. Emit order matches manifest order so successive
    regenerations produce deterministic VFS contents.
    """

    mount_path: tuple[str, ...]
    api: APIModel
    raw_spec: dict[str, Any] | str | Path
    spec_index: int


def mount_segments(namespace: str) -> tuple[str, ...]:
    """Split a dotted manifest mount string into its segment tuple.

    `""` (or whitespace-only) returns the empty tuple — the root mount.
    Leading / trailing dots are stripped before splitting so a typo like
    ``.users`` doesn't produce a phantom empty leading segment.

    Args:
        namespace: the ``specs[].namespace`` value from the manifest.

    Returns:
        Tuple of segments suitable for path joining and topology checks.
    """
    cleaned = namespace.strip().strip(".")
    if not cleaned:
        return ()
    return tuple(cleaned.split("."))


def mount_relpath(mount_path: Sequence[str]) -> str:
    """Render a mount tuple as a POSIX relative path component.

    The root mount produces the empty string so callers can build paths
    via simple concatenation (``f"src/{pkg}/base/{relpath}collections/..."``)
    without a leading slash when there is no mount segment to prepend.

    Args:
        mount_path: the mount tuple, typically `MountedSpec.mount_path`.

    Returns:
        ``"users/"`` for `("users",)`, ``"platform/users/"`` for
        `("platform", "users")`, ``""`` for `()`.
    """
    if not mount_path:
        return ""
    return "/".join(mount_path) + "/"


def check_mount_collisions(mounts: Sequence[MountedSpec]) -> None:
    """Defensive cross-mount uniqueness check.

    The manifest loader already enforces this; running it again at the
    composition boundary catches programmatic construction paths that
    sidestep the loader (tests, embedded callers).

    Raises:
        GenerationError: when two entries share the same `mount_path`.
    """
    seen: dict[tuple[str, ...], int] = {}
    for mount in mounts:
        if mount.mount_path in seen:
            first = seen[mount.mount_path]
            label = ".".join(mount.mount_path) if mount.mount_path else "<root>"
            raise GenerationError(
                f"mount namespace collision: specs[{first}] and "
                f"specs[{mount.spec_index}] both mount at {label!r}"
            )
        seen[mount.mount_path] = mount.spec_index


def plan_mounts(manifest: GenerationManifest) -> list[MountedSpec]:
    """Parse every spec in `manifest` and tag each result with its mount path.

    Per-spec inputs (`rules`, `strip_prefix`, `unmatched`, `lang`) come from
    the spec entry; `nlp_cache_dir` is project-wide. Dotted mounts are
    currently rejected — the synthetic intermediate-namespace machinery
    they would require is not yet wired through `emit_client`. Defensive
    cross-mount-collision check runs after parsing so manifests that
    bypassed `load_manifest` (programmatic construction) still fail
    cleanly.

    Args:
        manifest: the validated project manifest.

    Returns:
        One `MountedSpec` per `specs[]` entry, in manifest order.

    Raises:
        GenerationError: dotted mount namespaces (two or more segments)
            are not yet supported; mount-path collisions across entries.
    """
    nlp_cache_dir = (
        manifest.nlp_cache_dir
        if manifest.nlp_cache_dir is not None
        else Path.cwd() / ".spacy"
    )
    multi_spec = len(manifest.specs) > 1
    mounts: list[MountedSpec] = []
    for index, entry in enumerate(manifest.specs):
        segments = mount_segments(entry.namespace)
        if len(segments) > 1:
            raise GenerationError(
                f"dotted mount namespace 'platform.users'-style is not yet "
                f"supported (specs[{index}] declares {entry.namespace!r}); "
                f"use a single-segment mount or '' for the root mount."
            )
        # Tag parser log records with the mount name so warnings from
        # interleaved specs in a multi-mount run stay attributable.
        # Single-spec manifests skip the prefix — there's only one
        # source, the user knows which one.
        tag = entry.namespace or "root" if multi_spec else None
        with _maybe_source_context(tag):
            api = parse_spec(
                entry.source,
                rules=entry.rules,
                lang=entry.lang or manifest.lang,
                strip_prefix=entry.strip_prefix,
                nlp_cache_dir=nlp_cache_dir,
                unmatched_namespace=entry.unmatched,
            )
        mounts.append(
            MountedSpec(
                mount_path=segments,
                api=api,
                raw_spec=entry.source,
                spec_index=index,
            )
        )
    check_mount_collisions(mounts)
    return mounts


@contextmanager
def _maybe_source_context(tag: str | None) -> Iterator[None]:
    """`source_context(tag)` when `tag` is set; a transparent no-op otherwise."""
    if tag is None:
        yield
        return
    with source_context(tag):
        yield


def mount_segment_name(mount_path: Sequence[str]) -> str:
    """Return the leaf segment of `mount_path`, or `""` for the root mount.

    Used to derive the synthetic mount-namespace class name and accessor
    attribute. `("auth",)` → `"auth"`; `()` → `""`.
    """
    return mount_path[-1] if mount_path else ""


def synthesize_mount_namespace(mount: MountedSpec) -> Namespace:
    """Build a synthetic `Namespace` that wraps `mount.api`'s top-level tree.

    The synthetic node carries the spec's top-level namespaces, collections,
    singletons, and actions as its children. The generator renders it through
    the existing `namespace.py.jinja` template, but writes the result to
    `base/<mount>/__init__.py` instead of `base/<mount>/namespaces/<name>.py`
    so the mount-namespace class sits at the import root of its sub-package.

    The returned node is fresh — `mount.api` is not mutated, and the
    children inside are the same references as the original (no copies),
    so any downstream emitter that reads `node.summary` / `node.path` /
    `node.actions` sees the same data.
    """
    name = mount_segment_name(mount.mount_path)
    return Namespace(
        name=name,
        namespaces=list(mount.api.namespaces),
        collections=list(mount.api.collections),
        singletons=list(mount.api.singletons),
        actions=list(mount.api.actions),
    )


def mount_class_name(mount_namespace: Namespace) -> str:
    """Return the class name for a synthetic mount namespace.

    Uses the `Mount` suffix (not `Namespace`) so the class never collides
    with a spec-internal namespace that happens to share the mount's
    name. A spec mounted as `auth` whose top-level contains a real
    `auth` namespace produces `AuthMountBase` (the mount) alongside
    `AuthNamespaceBase` (the spec's namespace) — distinct symbols at
    every layer.
    """
    return f"{_pascal_segment(mount_namespace.name)}MountBase"


def _pascal_segment(name: str) -> str:
    """PascalCase a mount segment for use in a class name.

    Splits on `-` / `_` / `.` and capitalizes each token, matching the
    convention used elsewhere in the generator without taking a
    dependency on the Jinja filter that lives inside `templating`.
    """
    parts = [p for p in name.replace("-", "_").replace(".", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def iter_mount_namespace_prefixes(
    mounts: Sequence[MountedSpec],
) -> list[tuple[str, ...]]:
    """Enumerate every distinct mount-namespace prefix in deterministic order.

    For mounts ``platform.users``, ``platform.billing``, and ``audit`` the
    result is ``[("audit",), ("platform",), ("platform", "billing"),
    ("platform", "users")]`` — sorted lexicographically by prefix so the
    client class's accessor tree emits in a stable, repeatable order. The
    empty prefix (root mount) is *not* included because it carries no
    synthetic namespace; its content sits directly on the client.

    Args:
        mounts: the planned list of `MountedSpec`s.

    Returns:
        Sorted list of unique non-empty prefix tuples covering every
        intermediate and leaf segment introduced by the mounts.
    """
    prefixes: set[tuple[str, ...]] = set()
    for mount in mounts:
        for depth in range(1, len(mount.mount_path) + 1):
            prefixes.add(mount.mount_path[:depth])
    return sorted(prefixes)
