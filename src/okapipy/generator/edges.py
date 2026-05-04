"""Manifest edge computation: walks the parser tree and emits one `Edge` per wiring.

Lives in its own module so `manifest.py` can stay free of dependencies on
`emit/stubs.py` and `emit/walk.py`. Without that split the import graph cycles
(`vfs → manifest → stubs → vfs`), forcing function-local imports inside
`manifest.py`. By concentrating the stubs/walk dependency here — at a higher
layer than `vfs.py` — the cycle disappears and every import can stay at module
scope.

`compute_edges` mirrors the auto-wiring logic in `emit/stubs.py`: each entry in
the returned list represents the same `__<factory>__ = ChildClass` line a stub
would emit. Drift detection on a later run computes `current - previous` over
the manifest's `edges` field to surface child wirings the user hasn't yet
hand-edited into their one-shot stubs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from okapipy.generator.emit.stubs import (
    ChildWiring,
    client_wirings,
    collection_wirings,
    namespace_wirings,
    resource_wirings,
)
from okapipy.generator.emit.walk import (
    collection_module,
    namespace_module,
    resource_module,
)
from okapipy.generator.manifest import GENERATOR_VERSION, Edge, Manifest
from okapipy.parser.model import APIModel, Collection, Namespace, Resource


def compute_manifest(
    api: APIModel,
    package: str,
    base_files: list[str],
) -> Manifest:
    """Build the manifest for the current generation.

    `base_files` is the list of POSIX-style VFS keys that fall inside `base/`.
    The `generated_at` timestamp uses ISO-8601 with second precision in UTC so
    the manifest is reproducible across runs that happen within the same second.
    """
    return Manifest(
        generator_version=GENERATOR_VERSION,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        base_files=sorted(base_files),
        edges=compute_edges(api, package),
    )


def compute_edges(api: APIModel, package: str) -> list[Edge]:
    """Walk the parser tree and return one `Edge` per parent → child wiring."""
    out: list[Edge] = []
    for w in client_wirings(api, package):
        out.append(edge_from_wiring("client.py", w, package))
    for ns in api.namespaces:
        walk_namespace(ns, out, package)
    for coll in api.collections:
        walk_collection(coll, out, package)
    return out


def edge_from_wiring(
    parent_module: str,
    wiring: ChildWiring,
    package: str,
) -> Edge:
    """Translate a `ChildWiring` into a manifest `Edge`.

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


def walk_namespace(ns: Namespace, out: list[Edge], package: str) -> None:
    """Recurse through a namespace, recording outgoing edges."""
    parent_module = f"namespaces/{namespace_module(ns)}.py"
    for w in namespace_wirings(ns, package):
        out.append(edge_from_wiring(parent_module, w, package))
    for child in ns.namespaces:
        walk_namespace(child, out, package)
    for coll in ns.collections:
        walk_collection(coll, out, package)


def walk_collection(coll: Collection, out: list[Edge], package: str) -> None:
    """Recurse through a collection, recording outgoing edges."""
    parent_module = f"collections/{collection_module(coll)}.py"
    for w in collection_wirings(coll, package):
        out.append(edge_from_wiring(parent_module, w, package))
    if coll.resource is not None:
        walk_resource(coll.resource, out, package)


def walk_resource(resource: Resource, out: list[Edge], package: str) -> None:
    """Recurse through a resource, recording outgoing edges."""
    parent_module = f"resources/{resource_module(resource)}.py"
    for w in resource_wirings(resource, package):
        out.append(edge_from_wiring(parent_module, w, package))
    for coll in resource.collections:
        walk_collection(coll, out, package)
