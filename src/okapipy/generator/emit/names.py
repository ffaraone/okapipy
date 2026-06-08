"""Naming helpers for emitted classes, modules, attributes, and import paths.

Every generated artifact (class name, file name, property name, factory hook,
import-dot prefix) is computed from a parser node's `name` or `path` by one of
the helpers in this module. Keeping them together makes the project's naming
conventions reviewable in one place and decouples them from the walker that
orchestrates emission.
"""

from __future__ import annotations

import re

from okapipy.generator.templating import snake_case
from okapipy.parser.model import Action, Collection, Namespace, Resource, Singleton


def factory_attr(attr: str) -> str:
    """Return the dunder-protected factory hook name for a child reachable as `attr`.

    Dunder-both-sides (`__orders_factory__`) does not trigger Python's name
    mangling, so the same attribute name is read with `self.__orders_factory__`
    from inside the base class and overridden as `__orders_factory__ = MyOrders`
    in a subclass without any `_ClassName__...` prefix dance.
    """
    return f"__{attr}_factory__"


def runtime_dots(mount_relpath: str) -> str:
    """Return the dot prefix templates use to import the shared base-level modules.

    Cross-mount-shared modules (`client.py`, `exceptions.py`, `filters.py`,
    `sort.py`, `transport.py`, `types.py`) live at `base/`; emitted files
    live at `base/[<mount_path>/]<subdir>/`. Each `<subdir>` is one
    additional package level, so the relative import needs `len(mount_path)
    + 2` dots (the `+ 2` covers the leaf subdir and `base/` itself).

    For the root mount (`mount_relpath == ""`) the result is `".."` — the
    today's value — so root-mounted projects render byte-identical files.
    """
    return "." * (mount_relpath.count("/") + 2)


def namespace_class(ns: Namespace) -> str:
    return f"{_pascal(ns.name)}NamespaceBase"


def namespace_module(ns: Namespace) -> str:
    return snake_case(ns.name)


def collection_class(coll: Collection) -> str:
    return f"{coll.name}CollectionBase"


def collection_module(coll: Collection) -> str:
    return snake_case(coll.name)


def collection_attr(coll: Collection) -> str:
    return snake_case(_path_segment(coll.path))


def resource_class(resource: Resource) -> str:
    return f"{resource.name}ResourceBase"


def resource_module(resource: Resource) -> str:
    return snake_case(resource.name)


def singleton_class(singleton: Singleton) -> str:
    return f"{singleton.name}SingletonBase"


def singleton_module(singleton: Singleton) -> str:
    return snake_case(singleton.name)


def singleton_attr(singleton: Singleton) -> str:
    return snake_case(_path_segment(singleton.path))


def action_class(action: Action) -> str:
    return f"{action.name}ActionBase"


def action_module(action: Action) -> str:
    return snake_case(action.name)


def action_attr(action: Action) -> str:
    if action.attr_override:
        return snake_case(action.attr_override)
    return snake_case(_path_segment(action.path))


def new_path_param(parent_path: str, child_path: str) -> str:
    """Extract the new `{name}` introduced when descending parent → child path.

    Falls back to `id` when no `{...}` segment can be detected (defensive — the
    parser shouldn't produce such a tree, but we don't want to crash on edge
    cases).
    """
    if not child_path.startswith(parent_path):
        match = re.search(r"\{([^}]+)\}", child_path)
        return match.group(1) if match else "id"
    suffix = child_path[len(parent_path) :].strip("/")
    match = re.match(r"^\{([^}]+)\}", suffix)
    return match.group(1) if match else "id"


def _pascal(value: str) -> str:
    """Local PascalCase helper used by namespace class naming."""
    return "".join(part.capitalize() for part in snake_case(value).split("_") if part)


def _path_segment(path: str) -> str:
    """Return the last non-template segment of `path` for property naming."""
    for segment in reversed([s for s in path.split("/") if s]):
        if segment.startswith("{") and segment.endswith("}"):
            continue
        return segment
    return ""
