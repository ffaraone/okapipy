"""Cross-run manifest: tracks regenerated files and parser-tree edges.

The manifest is written to `src/{package_path}/base/_manifest.json` on every
generation. It serves two operational purposes:

* **Pruning.** `base_files` is the set of files the regenerated tree owns. On
  the next generation, `previous.base_files - current.base_files` is the set
  of stale files (their parser-tree node no longer exists in the spec) that
  `write_to_disk` deletes from disk. User-layer files are never tracked here
  and never pruned.
* **Drift detection.** `edges` is the set of parent → child wirings the
  current parser tree implies. On the next generation,
  `current.edges - previous.edges` are NEW children whose user-layer parent
  stub (one-shot, never overwritten) doesn't yet wire them; the difference
  is converted to warnings telling the user the exact one-line edit to make.

Edges are stored *abstractly* — one entry per sync/async pair, not per
emitted Python class. The drift-warning formatter expands one Edge into the
two lines (sync + async) the user needs to add.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING

from okapipy.parser.model import APIModel, Collection, Namespace, Resource

if TYPE_CHECKING:
    from okapipy.generator.emit.stubs import _ChildWiring


def _generator_version() -> str:
    """Return the installed okapipy version, falling back to `0.0.0+unknown`.

    Resolved at import time from package metadata so the manifest always carries
    the version of the okapipy that produced it. The fallback covers running
    against a source tree that hasn't been installed (e.g. some packaging tests).
    """
    try:
        return version("okapipy")
    except PackageNotFoundError:
        return "0.0.0+unknown"


GENERATOR_VERSION = _generator_version()
"""Version string written into every manifest. Sourced from package metadata."""

MANIFEST_FILENAME = "_manifest.json"
"""Filename inside `<package>/base/` where the manifest is stored."""


@dataclass(frozen=True)
class Edge:
    """One parent → child wiring in the parser tree.

    `parent_module` is the user-layer relative path (`client.py`,
    `namespaces/commerce.py`, `collections/orders.py`, …) — i.e. where the
    drift detector looks to check if the user has wired the factory.
    `child_user_class` is the *sync* user-layer class name; the async sibling
    is implicit (`Async` + the same name).
    """

    parent_module: str
    factory_attr: str
    child_user_class: str
    child_user_module: str


@dataclass(frozen=True)
class Manifest:
    """The full manifest written under `base/_manifest.json`."""

    generator_version: str
    generated_at: str
    base_files: list[str]
    edges: list[Edge]


def compute_manifest(
    api: APIModel,
    package: str,
    base_files: list[str],
) -> Manifest:
    """Build the manifest for the current generation.

    `base_files` is the list of POSIX-style VFS keys that fall inside `base/`.
    """
    return Manifest(
        generator_version=GENERATOR_VERSION,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        base_files=sorted(base_files),
        edges=compute_edges(api, package),
    )


def compute_edges(api: APIModel, package: str) -> list[Edge]:
    """Walk the parser tree and return one `Edge` per parent → child wiring.

    Mirrors the auto-wiring logic in `emit/stubs.py` so the manifest's
    `edges` list is exactly the set of `__<factory>__ = ChildClass` lines
    the stubs emit. Drift detection on a later run computes
    `current - previous` over this set.
    """
    # Local import to break the `vfs → manifest → stubs → vfs` cycle.
    from okapipy.generator.emit.stubs import _client_wirings

    out: list[Edge] = []
    for w in _client_wirings(api, package):
        out.append(_edge_from_wiring("client.py", w, package))
    for ns in api.namespaces:
        _walk_namespace(ns, out, package)
    for coll in api.collections:
        _walk_collection(coll, out, package)
    return out


def serialize(manifest: Manifest) -> str:
    """Render the manifest as a deterministic JSON string."""
    payload = asdict(manifest)
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def parse(text: str) -> Manifest:
    """Parse a manifest JSON string into a `Manifest`. Tolerant of unknown keys."""
    data = json.loads(text)
    edges = [Edge(**e) for e in data.get("edges", [])]
    return Manifest(
        generator_version=data.get("generator_version", ""),
        generated_at=data.get("generated_at", ""),
        base_files=list(data.get("base_files", [])),
        edges=edges,
    )


def read_from_disk(manifest_path: Path) -> Manifest | None:
    """Read a manifest from disk; return None when the file does not exist."""
    if not manifest_path.exists():
        return None
    try:
        return parse(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _edge_from_wiring(
    parent_module: str,
    wiring: _ChildWiring,
    package: str,
) -> Edge:
    """Translate a `_ChildWiring` into a manifest `Edge`.

    Strips the dotted `package.` prefix from `user_module_path` and converts
    dots to slashes so the stored value matches the user-layer relative path
    on disk.
    """
    rel = wiring.user_module_path.removeprefix(f"{package}.").replace(".", "/")
    return Edge(
        parent_module=parent_module,
        factory_attr=wiring.factory_attr,
        child_user_class=wiring.user_class,
        child_user_module=f"{rel}.py",
    )


def _walk_namespace(ns: Namespace, out: list[Edge], package: str) -> None:
    """Recurse through a namespace, recording outgoing edges."""
    from okapipy.generator.emit.stubs import _namespace_wirings

    parent_module = f"namespaces/{_module_for_ns(ns)}.py"
    for w in _namespace_wirings(ns, package):
        out.append(_edge_from_wiring(parent_module, w, package))
    for child in ns.namespaces:
        _walk_namespace(child, out, package)
    for coll in ns.collections:
        _walk_collection(coll, out, package)


def _walk_collection(coll: Collection, out: list[Edge], package: str) -> None:
    """Recurse through a collection, recording outgoing edges."""
    from okapipy.generator.emit.stubs import _collection_wirings

    parent_module = f"collections/{_module_for_coll(coll)}.py"
    for w in _collection_wirings(coll, package):
        out.append(_edge_from_wiring(parent_module, w, package))
    if coll.resource is not None:
        _walk_resource(coll.resource, out, package)


def _walk_resource(resource: Resource, out: list[Edge], package: str) -> None:
    """Recurse through a resource, recording outgoing edges."""
    from okapipy.generator.emit.stubs import _resource_wirings

    parent_module = f"resources/{_module_for_res(resource)}.py"
    for w in _resource_wirings(resource, package):
        out.append(_edge_from_wiring(parent_module, w, package))
    for coll in resource.collections:
        _walk_collection(coll, out, package)


# Lazy imports of the snake_case helpers to avoid a circular import at module load.
def _module_for_ns(ns: Namespace) -> str:
    from okapipy.generator.emit.walk import _namespace_module

    return _namespace_module(ns)


def _module_for_coll(coll: Collection) -> str:
    from okapipy.generator.emit.walk import _collection_module

    return _collection_module(coll)


def _module_for_res(resource: Resource) -> str:
    from okapipy.generator.emit.walk import _resource_module

    return _resource_module(resource)
