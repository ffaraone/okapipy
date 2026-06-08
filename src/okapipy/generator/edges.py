"""Generated-state edge computation: walks the parser tree and emits one `Edge` per wiring.

Lives in its own module so `state.py` can stay free of dependencies on
`emit/stubs.py` and `emit/walk.py`. Without that split the import graph cycles
(`vfs → state → stubs → vfs`), forcing function-local imports inside
`state.py`. By concentrating the stubs/walk dependency here — at a higher
layer than `vfs.py` — the cycle disappears and every import can stay at module
scope.

`compute_edges` mirrors the auto-wiring logic in `emit/stubs.py`: each entry in
the returned list represents the same `__<factory>__ = ChildClass` line a stub
would emit. Drift detection on a later run computes `current - previous` over
the generated-state file's `edges` field to surface child wirings the user
hasn't yet hand-edited into their one-shot stubs.
"""

from __future__ import annotations

from datetime import UTC, datetime

from okapipy.generator.emit.names import (
    collection_module,
    namespace_module,
    resource_module,
    singleton_module,
)
from okapipy.generator.emit.stubs import (
    ChildWiring,
    client_wirings,
    collection_wirings,
    namespace_wirings,
    resource_wirings,
    singleton_wirings,
)
from okapipy.generator.state import GENERATOR_VERSION, Edge, GeneratedState
from okapipy.parser.model import APIModel, Collection, Namespace, Resource, Singleton


def compute_state(
    api: APIModel,
    package: str,
    base_files: list[str],
    *,
    mount_relpath: str = "",
) -> GeneratedState:
    """Build the generated-state record for the current generation.

    `base_files` is the list of POSIX-style VFS keys that fall inside `base/`.
    The `generated_at` timestamp uses ISO-8601 with second precision in UTC so
    the state record is reproducible across runs that happen within the same
    second.

    `mount_relpath` is forwarded to `compute_edges` so multi-mount projects
    record mount-prefixed parent/child module paths.
    """
    return GeneratedState(
        generator_version=GENERATOR_VERSION,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        base_files=sorted(base_files),
        edges=compute_edges(api, package, mount_relpath=mount_relpath),
    )


def compute_edges(
    api: APIModel, package: str, *, mount_relpath: str = ""
) -> list[Edge]:
    """Walk the parser tree and return one `Edge` per parent → child wiring.

    `mount_relpath` lets multi-mount projects record `parent_module` paths
    that include the mount sub-directory (`users/namespaces/foo.py`), so
    drift detection finds the right user-layer stub on disk.

    Top-level wirings for the root mount (`mount_relpath == ""`) parent
    on `client.py`; for non-root mounts they parent on the mount
    namespace stub at `<mount_relpath>__init__.py`, since that is where
    the user-layer `<Mount>Mount` subclass that owns those wirings
    actually lives.
    """
    top_parent = f"{mount_relpath}__init__.py" if mount_relpath else "client.py"
    out: list[Edge] = []
    for w in client_wirings(api, package, mount_relpath=mount_relpath):
        out.append(edge_from_wiring(top_parent, w, package))
    for ns in api.namespaces:
        walk_namespace(ns, out, package, mount_relpath)
    for coll in api.collections:
        walk_collection(coll, out, package, mount_relpath)
    for sing in api.singletons:
        walk_singleton(sing, out, package, mount_relpath)
    return out


def edge_from_wiring(
    parent_module: str,
    wiring: ChildWiring,
    package: str,
) -> Edge:
    """Translate a `ChildWiring` into a generated-state `Edge`.

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


def walk_namespace(
    ns: Namespace, out: list[Edge], package: str, mount_relpath: str = ""
) -> None:
    """Recurse through a namespace, recording outgoing edges."""
    parent_module = f"{mount_relpath}namespaces/{namespace_module(ns)}.py"
    for w in namespace_wirings(ns, package, mount_relpath=mount_relpath):
        out.append(edge_from_wiring(parent_module, w, package))
    for child in ns.namespaces:
        walk_namespace(child, out, package, mount_relpath)
    for coll in ns.collections:
        walk_collection(coll, out, package, mount_relpath)
    for sing in ns.singletons:
        walk_singleton(sing, out, package, mount_relpath)


def walk_collection(
    coll: Collection, out: list[Edge], package: str, mount_relpath: str = ""
) -> None:
    """Recurse through a collection, recording outgoing edges."""
    parent_module = f"{mount_relpath}collections/{collection_module(coll)}.py"
    for w in collection_wirings(coll, package, mount_relpath=mount_relpath):
        out.append(edge_from_wiring(parent_module, w, package))
    if coll.resource is not None:
        walk_resource(coll.resource, out, package, mount_relpath)


def walk_resource(
    resource: Resource, out: list[Edge], package: str, mount_relpath: str = ""
) -> None:
    """Recurse through a resource, recording outgoing edges."""
    parent_module = f"{mount_relpath}resources/{resource_module(resource)}.py"
    for w in resource_wirings(resource, package, mount_relpath=mount_relpath):
        out.append(edge_from_wiring(parent_module, w, package))
    for coll in resource.collections:
        walk_collection(coll, out, package, mount_relpath)
    for sing in resource.singletons:
        walk_singleton(sing, out, package, mount_relpath)


def walk_singleton(
    singleton: Singleton, out: list[Edge], package: str, mount_relpath: str = ""
) -> None:
    """Recurse through a singleton, recording outgoing edges."""
    parent_module = f"{mount_relpath}singletons/{singleton_module(singleton)}.py"
    for w in singleton_wirings(singleton, package, mount_relpath=mount_relpath):
        out.append(edge_from_wiring(parent_module, w, package))
    for coll in singleton.collections:
        walk_collection(coll, out, package, mount_relpath)
    for sub in singleton.singletons:
        walk_singleton(sub, out, package, mount_relpath)
