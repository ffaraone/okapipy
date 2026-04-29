"""Walk the parsed `APIModel` and emit one templated file per node.

The walker decides class names (`<ContextualPascalCase> + <Suffix>`), module names
(`<snake_case_of_class>` minus the suffix), property names on parents (snake_case
of the path segment), and the path-param introduced by each Resource (computed
by diffing parent collection's path).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jinja2 import Environment

from okapipy.generator.templating import _snake_case, render_python
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Operation,
    Resource,
)


@dataclass(frozen=True)
class _ChildRef:
    """Reference to a child node — used in template contexts for property emission."""

    attr: str          # snake_case property name
    class_name: str    # PascalCase class name including suffix
    module: str        # snake_case module name (no suffix, no extension)


def emit_tree(
    env: Environment,
    api: APIModel,
    project_context: Mapping[str, Any],
    package_path: str,
) -> dict[str, str]:
    """Render every namespace, collection, resource, and action in `api`."""
    out: dict[str, str] = {}
    # Top-level namespaces.
    for ns in api.namespaces:
        out.update(_emit_namespace(env, ns, project_context, package_path))
    # Top-level collections.
    for coll in api.collections:
        out.update(_emit_collection(env, coll, project_context, package_path))
    return out


def emit_root_init_extension(api: APIModel) -> tuple[list[str], list[str]]:
    """Return import lines and public names for top-level namespace/collection re-exports.

    The runtime emitter splices both into the package `__init__.py`: imports
    appear after the runtime imports, and the public names are merged into the
    single `__all__` literal (so ruff's F401 stays happy without redundant
    `Foo as Foo` aliases or `__all__.extend(...)` runtime mutation).
    """
    import_lines: list[str] = []
    public_names: list[str] = []
    for ns in api.namespaces:
        cls = _namespace_class(ns)
        import_lines.append(
            f"from .namespaces.{_namespace_module(ns)} import {cls}, Async{cls}"
        )
        public_names.append(cls)
        public_names.append(f"Async{cls}")
    for coll in api.collections:
        cls = _collection_class(coll)
        import_lines.append(
            f"from .collections.{_collection_module(coll)} import {cls}, Async{cls}"
        )
        public_names.append(cls)
        public_names.append(f"Async{cls}")
    return import_lines, public_names


# --------------------------------------------------------------------------- #
# Namespace                                                                   #
# --------------------------------------------------------------------------- #


def _emit_namespace(
    env: Environment,
    ns: Namespace,
    project_context: Mapping[str, Any],
    package_path: str,
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_namespaces: list[_ChildRef] = [
        _ChildRef(
            attr=_snake_case(child.name),
            class_name=_namespace_class(child),
            module=_namespace_module(child),
        )
        for child in ns.namespaces
    ]
    child_collections: list[_ChildRef] = [
        _ChildRef(
            attr=_collection_attr(coll),
            class_name=_collection_class(coll),
            module=_collection_module(coll),
        )
        for coll in ns.collections
    ]
    ctx = {
        **project_context,
        "class_name": _namespace_class(ns),
        "child_namespaces": child_namespaces,
        "child_collections": child_collections,
    }
    out[f"src/{package_path}/namespaces/{_namespace_module(ns)}.py"] = render_python(
        env, "package/namespace.py.jinja", ctx
    )
    for child in ns.namespaces:
        out.update(_emit_namespace(env, child, project_context, package_path))
    for coll in ns.collections:
        out.update(_emit_collection(env, coll, project_context, package_path))
    return out


# --------------------------------------------------------------------------- #
# Collection                                                                  #
# --------------------------------------------------------------------------- #


def _emit_collection(
    env: Environment,
    coll: Collection,
    project_context: Mapping[str, Any],
    package_path: str,
) -> dict[str, str]:
    out: dict[str, str] = {}
    resource_ref: dict[str, str] | None = None
    if coll.resource is not None:
        id_param = _new_path_param(coll.path, coll.resource.path)
        resource_ref = {
            "class_name": _resource_class(coll.resource),
            "module": _resource_module(coll.resource),
            "id_param": id_param,
        }
    actions = [
        {
            "attr": _action_attr(action),
            "class_name": _action_class(action),
            "module": _action_module(action),
        }
        for action in coll.actions
    ]
    fetch_op = coll.fetch
    create_op = coll.create
    # Collection imports only what its emitted code references. The fetch
    # response model is the *envelope*; iteration calls `from_response(None, ...)`
    # because the parser doesn't expose item types yet (Phase 7).
    model_imports = sorted(_collect_model_names([create_op]))
    ctx = {
        **project_context,
        "class_name": _collection_class(coll),
        "path_template": coll.path,
        "resource": resource_ref,
        "actions": actions,
        "create_op": _op_context(create_op) if create_op is not None else None,
        "fetch_response_model": fetch_op.response_model if fetch_op is not None else None,
        "pagination_supported": (
            fetch_op.pagination_supported if fetch_op is not None else False
        ),
        "filter_supported": fetch_op.filter_supported if fetch_op is not None else False,
        "sort_supported": fetch_op.sort_supported if fetch_op is not None else False,
        "supports_count": (
            fetch_op.pagination_supported if fetch_op is not None else False
        ),
        "model_imports": model_imports,
    }
    out[f"src/{package_path}/collections/{_collection_module(coll)}.py"] = render_python(
        env, "package/collection.py.jinja", ctx
    )
    if coll.resource is not None:
        out.update(_emit_resource(env, coll, coll.resource, project_context, package_path))
    for action in coll.actions:
        out.update(_emit_action(env, action, project_context, package_path))
    return out


# --------------------------------------------------------------------------- #
# Resource                                                                    #
# --------------------------------------------------------------------------- #


def _emit_resource(
    env: Environment,
    parent_coll: Collection,
    resource: Resource,
    project_context: Mapping[str, Any],
    package_path: str,
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_collections = [
        _ChildRef(
            attr=_collection_attr(coll),
            class_name=_collection_class(coll),
            module=_collection_module(coll),
        )
        for coll in resource.collections
    ]
    actions = [
        _ChildRef(
            attr=_action_attr(action),
            class_name=_action_class(action),
            module=_action_module(action),
        )
        for action in resource.actions
    ]
    model_imports = sorted(
        _collect_model_names(
            [resource.retrieve, resource.update, resource.partial_update, resource.delete]
        )
    )
    ctx = {
        **project_context,
        "class_name": _resource_class(resource),
        "path_template": resource.path,
        "retrieve_op": _op_context(resource.retrieve),
        "update_op": _op_context(resource.update),
        "patch_op": _op_context(resource.partial_update),
        "delete_op": _op_context(resource.delete),
        "child_collections": child_collections,
        "actions": actions,
        "model_imports": model_imports,
    }
    out[f"src/{package_path}/resources/{_resource_module(resource)}.py"] = render_python(
        env, "package/resource.py.jinja", ctx
    )
    for coll in resource.collections:
        out.update(_emit_collection(env, coll, project_context, package_path))
    for action in resource.actions:
        out.update(_emit_action(env, action, project_context, package_path))
    _ = parent_coll  # parent context kept for future use (e.g. type hints)
    return out


# --------------------------------------------------------------------------- #
# Action                                                                      #
# --------------------------------------------------------------------------- #


def _emit_action(
    env: Environment,
    action: Action,
    project_context: Mapping[str, Any],
    package_path: str,
) -> dict[str, str]:
    operations = [_op_context(op) for op in action.operations]
    operations = [op for op in operations if op is not None]
    single_op = operations[0] if len(operations) == 1 else None
    model_imports = sorted(_collect_model_names(action.operations))
    ctx = {
        **project_context,
        "class_name": _action_class(action),
        "path_template": action.path,
        "operations": operations,
        "single_op": single_op,
        "model_imports": model_imports,
    }
    return {
        f"src/{package_path}/actions/{_action_module(action)}.py": render_python(
            env, "package/action.py.jinja", ctx
        ),
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _collect_model_names(operations: Sequence[Operation | None]) -> set[str]:
    """Return the set of Pydantic model names referenced by `operations`.

    Used to emit `from ..models import <names>` at the top of each generated
    collection / resource / action file. Empty when every operation is `None`
    or has no `request_model` / `response_model`.
    """
    names: set[str] = set()
    for op in operations:
        if op is None:
            continue
        if op.request_model:
            names.add(op.request_model)
        if op.response_model:
            names.add(op.response_model)
    return names


def _op_context(op: Operation | None) -> dict[str, Any] | None:
    """Translate an Operation into the small dict templates need."""
    if op is None:
        return None
    return {
        "method": op.method,
        "response_model": op.response_model,
        "request_model": op.request_model,
        "has_body": op.request_model is not None,
        "pagination_supported": op.pagination_supported,
        "filter_supported": op.filter_supported,
        "sort_supported": op.sort_supported,
    }


def _new_path_param(parent_path: str, child_path: str) -> str:
    """Extract the new `{name}` introduced when descending parent → child path.

    Falls back to `id` when no `{...}` segment can be detected (defensive — the
    parser shouldn't produce such a tree, but we don't want to crash on edge
    cases).
    """
    if not child_path.startswith(parent_path):
        # Best-effort: search the child for any `{name}` segment.
        match = re.search(r"\{([^}]+)\}", child_path)
        return match.group(1) if match else "id"
    suffix = child_path[len(parent_path) :].strip("/")
    match = re.match(r"^\{([^}]+)\}", suffix)
    return match.group(1) if match else "id"


def _path_segment(path: str) -> str:
    """Return the last non-template segment of `path` for property naming."""
    for segment in reversed([s for s in path.split("/") if s]):
        if segment.startswith("{") and segment.endswith("}"):
            continue
        return segment
    return ""


def _namespace_class(ns: Namespace) -> str:
    return f"{_pascal(ns.name)}Namespace"


def _namespace_module(ns: Namespace) -> str:
    return _snake_case(ns.name)


def _collection_class(coll: Collection) -> str:
    return f"{coll.name}Collection"


def _collection_module(coll: Collection) -> str:
    return _snake_case(coll.name)


def _collection_attr(coll: Collection) -> str:
    return _snake_case(_path_segment(coll.path))


def _resource_class(resource: Resource) -> str:
    return f"{resource.name}Resource"


def _resource_module(resource: Resource) -> str:
    return _snake_case(resource.name)


def _action_class(action: Action) -> str:
    return f"{action.name}Action"


def _action_module(action: Action) -> str:
    return _snake_case(action.name)


def _action_attr(action: Action) -> str:
    return _snake_case(_path_segment(action.path))


def _pascal(value: str) -> str:
    """Local PascalCase helper used by namespace class naming."""
    return "".join(part.capitalize() for part in _snake_case(value).split("_") if part)


# Stop the unused-import linter from complaining about `Iterable`; keep the
# alias in case a future caller switches the public API to an iterator return.
_ = Iterable
