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

    attr: str  # snake_case property name
    class_name: str  # PascalCase class name including the `Base` suffix
    module: str  # snake_case module name (no suffix, no extension)
    factory_attr: str  # dunder-protected ClassVar hook, e.g. `__orders_factory__`
    docstring: str | None = None  # docstring for the property accessor (indent=8)


def _factory_attr(attr: str) -> str:
    """Return the dunder-protected factory hook name for a child reachable as `attr`.

    Dunder-both-sides (`__orders_factory__`) does not trigger Python's name
    mangling, so the same attribute name is read with `self.__orders_factory__`
    from inside the base class and overridden as `__orders_factory__ = MyOrders`
    in a subclass without any `_ClassName__...` prefix dance.
    """
    return f"__{attr}_factory__"


def emit_tree(
    env: Environment,
    api: APIModel,
    project_context: Mapping[str, Any],
    package_path: str,
    available_models: set[str] | None = None,
) -> dict[str, str]:
    """Render every namespace, collection, resource, and action in `api`.

    `available_models` is the set of top-level identifiers actually emitted in
    `models.py` (computed by introspecting dmcg's output). When provided, model
    references not in the set are dropped from import lines and replaced with
    `None` in `response_model` slots — this prevents `ImportError` for schema
    names dmcg inlined or skipped (primitive aliases, empty objects, etc.).
    Passing `None` disables filtering (used in tests that mock the walker
    directly).
    """
    out: dict[str, str] = {}
    available = available_models if available_models is not None else None
    # Top-level namespaces.
    for ns in api.namespaces:
        out.update(_emit_namespace(env, ns, project_context, package_path, available))
    # Top-level collections.
    for coll in api.collections:
        out.update(
            _emit_collection(env, coll, project_context, package_path, available)
        )
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
    available_models: set[str] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_namespaces: list[_ChildRef] = [
        _ChildRef(
            attr=_snake_case(child.name),
            class_name=_namespace_class(child),
            module=_namespace_module(child),
            factory_attr=_factory_attr(_snake_case(child.name)),
        )
        for child in ns.namespaces
    ]
    child_collections: list[_ChildRef] = [
        _ChildRef(
            attr=_collection_attr(coll),
            class_name=_collection_class(coll),
            module=_collection_module(coll),
            factory_attr=_factory_attr(_collection_attr(coll)),
            docstring=collection_property_docstring(coll),
        )
        for coll in ns.collections
    ]
    ctx = {
        **project_context,
        "class_name": _namespace_class(ns),
        "child_namespaces": child_namespaces,
        "child_collections": child_collections,
        "class_docstring": build_docstring(
            ns.summary,
            ns.description,
            fallback=f"Namespace router for `{ns.name}`.",
        ),
    }
    out[f"src/{package_path}/base/namespaces/{_namespace_module(ns)}.py"] = (
        render_python(env, "package/namespace.py.jinja", ctx)
    )
    for child in ns.namespaces:
        out.update(
            _emit_namespace(env, child, project_context, package_path, available_models)
        )
    for coll in ns.collections:
        out.update(
            _emit_collection(env, coll, project_context, package_path, available_models)
        )
    return out


# --------------------------------------------------------------------------- #
# Collection                                                                  #
# --------------------------------------------------------------------------- #


def _emit_collection(
    env: Environment,
    coll: Collection,
    project_context: Mapping[str, Any],
    package_path: str,
    available_models: set[str] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    resource_ref: dict[str, str] | None = None
    if coll.resource is not None:
        id_param = _new_path_param(coll.path, coll.resource.path)
        resource_ref = {
            "class_name": _resource_class(coll.resource),
            "module": _resource_module(coll.resource),
            "id_param": id_param,
            "factory_attr": _factory_attr("resource"),
        }
    actions = [
        {
            "attr": _action_attr(action),
            "class_name": _action_class(action),
            "module": _action_module(action),
            "factory_attr": _factory_attr(_action_attr(action)),
        }
        for action in coll.actions
    ]
    fetch_op = coll.fetch
    create_op = coll.create
    # The fetch response model is the *envelope*; the iterator yields items
    # typed as `fetch_item_model` when the parser detected a list shape.
    fetch_item_model = (
        _filter_model_name(fetch_op.item_model, available_models)
        if fetch_op is not None
        else None
    )
    # Collection imports only what its emitted code references: the create
    # request/response models and (when present) the iterator's item type.
    import_names = _collect_model_names([create_op], available_models)
    if fetch_item_model is not None:
        import_names.add(fetch_item_model)
    model_imports = sorted(import_names)
    create_op_ctx = _op_context(create_op, available_models)
    # Class docstring comes from the fetch operation per generator.md §9. When
    # the operation isn't populated, fall back to a generic structural string.
    if fetch_op is not None:
        class_doc = build_docstring(
            fetch_op.summary,
            fetch_op.description,
            fallback=f"Collection at `{coll.path}`.",
        )
    else:
        class_doc = build_docstring(
            coll.summary,
            coll.description,
            fallback=f"Collection at `{coll.path}`.",
        )
    create_doc: str | None = None
    if create_op is not None:
        create_doc = build_docstring(
            create_op.summary,
            create_op.description,
            fallback=f"`{create_op.method}` body to {coll.path}.",
            indent=8,
        )
    ctx = {
        **project_context,
        "class_name": _collection_class(coll),
        "path_template": coll.path,
        "resource": resource_ref,
        "actions": actions,
        "create_op": create_op_ctx,
        "fetch_response_model": (
            _filter_model_name(fetch_op.response_model, available_models)
            if fetch_op is not None
            else None
        ),
        "fetch_item_model": fetch_item_model,
        "pagination_supported": (
            fetch_op.pagination_supported if fetch_op is not None else False
        ),
        "filter_supported": fetch_op.filter_supported
        if fetch_op is not None
        else False,
        "sort_supported": fetch_op.sort_supported if fetch_op is not None else False,
        "supports_count": (
            fetch_op.pagination_supported if fetch_op is not None else False
        ),
        "model_imports": model_imports,
        "class_docstring": class_doc,
        "create_docstring": create_doc,
    }
    out[f"src/{package_path}/base/collections/{_collection_module(coll)}.py"] = (
        render_python(env, "package/collection.py.jinja", ctx)
    )
    if coll.resource is not None:
        out.update(
            _emit_resource(
                env,
                coll,
                coll.resource,
                project_context,
                package_path,
                available_models,
            )
        )
    for action in coll.actions:
        out.update(
            _emit_action(env, action, project_context, package_path, available_models)
        )
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
    available_models: set[str] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_collections = [
        _ChildRef(
            attr=_collection_attr(coll),
            class_name=_collection_class(coll),
            module=_collection_module(coll),
            factory_attr=_factory_attr(_collection_attr(coll)),
            docstring=collection_property_docstring(coll),
        )
        for coll in resource.collections
    ]
    actions = [
        _ChildRef(
            attr=_action_attr(action),
            class_name=_action_class(action),
            module=_action_module(action),
            factory_attr=_factory_attr(_action_attr(action)),
        )
        for action in resource.actions
    ]
    model_imports = sorted(
        _collect_model_names(
            [
                resource.retrieve,
                resource.update,
                resource.partial_update,
                resource.delete,
            ],
            available_models,
        )
    )

    def _op_doc(op: Operation | None, suffix: str = "") -> str | None:
        if op is None:
            return None
        fallback = f"`{op.method} {resource.path}`{suffix}."
        return build_docstring(op.summary, op.description, fallback=fallback, indent=8)

    ctx = {
        **project_context,
        "class_name": _resource_class(resource),
        "path_template": resource.path,
        "retrieve_op": _op_context(resource.retrieve, available_models),
        "update_op": _op_context(resource.update, available_models),
        "patch_op": _op_context(resource.partial_update, available_models),
        "delete_op": _op_context(resource.delete, available_models),
        "child_collections": child_collections,
        "actions": actions,
        "model_imports": model_imports,
        "class_docstring": build_docstring(
            resource.summary,
            resource.description,
            fallback=f"Resource at `{resource.path}`.",
        ),
        "retrieve_docstring": _op_doc(resource.retrieve),
        "update_docstring": _op_doc(resource.update, " (full replacement)"),
        "patch_docstring": _op_doc(resource.partial_update, " (partial update)"),
        "delete_docstring": _op_doc(resource.delete),
    }
    out[f"src/{package_path}/base/resources/{_resource_module(resource)}.py"] = (
        render_python(env, "package/resource.py.jinja", ctx)
    )
    for coll in resource.collections:
        out.update(
            _emit_collection(env, coll, project_context, package_path, available_models)
        )
    for action in resource.actions:
        out.update(
            _emit_action(env, action, project_context, package_path, available_models)
        )
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
    available_models: set[str] | None,
) -> dict[str, str]:
    operations = [_op_context(op, available_models) for op in action.operations]
    operations = [op for op in operations if op is not None]
    single_op = operations[0] if len(operations) == 1 else None
    model_imports = sorted(_collect_model_names(action.operations, available_models))
    # Single op: class doc and the (sole) method's doc share the same source per
    # generator.md §11. Multi-op: class doc lists all operations; per-method docs
    # come from each operation individually.
    class_doc = build_action_docstring(action)
    op_docstrings: list[str] = []
    for op in action.operations:
        op_docstrings.append(
            build_docstring(
                op.summary,
                op.description,
                fallback=f"`{op.method} {action.path}`.",
                indent=8,
            )
        )
    single_op_docstring = op_docstrings[0] if len(op_docstrings) == 1 else None
    ctx = {
        **project_context,
        "class_name": _action_class(action),
        "path_template": action.path,
        "operations": operations,
        "single_op": single_op,
        "model_imports": model_imports,
        "class_docstring": class_doc,
        "single_op_docstring": single_op_docstring,
        "op_docstrings": op_docstrings,
    }
    return {
        f"src/{package_path}/base/actions/{_action_module(action)}.py": render_python(
            env, "package/action.py.jinja", ctx
        ),
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def build_docstring(
    summary: str | None,
    description: str | None,
    fallback: str,
    indent: int = 4,
) -> str:
    """Format a Python docstring from OpenAPI `summary` + `description`.

    Returns a triple-quoted block ready to splice into a generated source file.
    `indent` controls left padding (4 for class docstrings, 8 for method
    docstrings). Falls back to `fallback` when both inputs are empty.
    """
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if description and description.strip():
        parts.append(description.strip())
    body = "\n\n".join(parts) if parts else fallback
    return _build_docstring_from_body(body, indent)


def collection_property_docstring(coll: Collection, indent: int = 8) -> str | None:
    """Docstring for a property that exposes a `Collection`.

    Per generator.md §7.7, the property accessor (e.g. `Admin.accounts`,
    `Order.lines`, `client.orders`) carries the docstring of the collection's
    `fetch` operation so users see the collection's purpose at the call site.
    Falls back to a structural string when the collection has no fetch op or
    the fetch op has no documentation.
    """
    fetch = coll.fetch
    if fetch is not None:
        return build_docstring(
            fetch.summary,
            fetch.description,
            fallback=f"Collection at `{coll.path}`.",
            indent=indent,
        )
    return build_docstring(
        coll.summary,
        coll.description,
        fallback=f"Collection at `{coll.path}`.",
        indent=indent,
    )


def build_action_docstring(action: Action, indent: int = 4) -> str:
    """Format an action class docstring: single-op uses the op's text, multi-op lists them."""
    if not action.operations:
        return build_docstring(
            action.summary,
            action.description,
            f"Action at `{action.path}`.",
            indent,
        )
    if len(action.operations) == 1:
        op = action.operations[0]
        return build_docstring(
            op.summary,
            op.description,
            f"Action at `{action.path}`.",
            indent,
        )
    header: list[str] = []
    if action.summary and action.summary.strip():
        header.append(action.summary.strip())
    elif action.description and action.description.strip():
        header.append(action.description.strip())
    else:
        header.append(f"Action at `{action.path}`.")
    header.append("")
    header.append("Operations:")
    for op in action.operations:
        summary = (op.summary or "").strip() or "(no summary)"
        header.append(f"- `{op.method}`: {summary}")
    return _build_docstring_from_body("\n".join(header), indent)


def _build_docstring_from_body(body: str, indent: int) -> str:
    """Wrap `body` in a triple-quoted block at the given indent level."""
    body = body.replace('"""', "'''")
    pad = " " * indent
    lines = body.split("\n")
    if len(lines) == 1 and len(lines[0]) + indent + 6 <= 100:
        return f'{pad}"""{lines[0]}"""'
    first = lines[0]
    rest = lines[1:]
    out_lines = [f'{pad}"""{first}']
    for line in rest:
        out_lines.append(pad + line if line.strip() else "")
    out_lines.append(f'{pad}"""')
    return "\n".join(out_lines)


def _collect_model_names(
    operations: Sequence[Operation | None],
    available_models: set[str] | None,
) -> set[str]:
    """Return the set of Pydantic model names referenced by `operations`.

    Used to emit `from ..models import <names>` at the top of each generated
    collection / resource / action file. Empty when every operation is `None`
    or has no `request_model` / `response_model`. Names not in `available_models`
    are filtered out so the generated import line never references a symbol
    dmcg didn't actually emit (e.g. schemas inlined as primitive aliases).
    """
    names: set[str] = set()
    for op in operations:
        if op is None:
            continue
        if op.request_model:
            names.add(op.request_model)
        if op.response_model:
            names.add(op.response_model)
    if available_models is not None:
        names &= available_models
    return names


def _filter_model_name(
    name: str | None, available_models: set[str] | None
) -> str | None:
    """Return `name` if dmcg emitted a matching symbol; otherwise `None`.

    A `None` response_model causes the runtime `from_response` to short-circuit
    and yield raw dicts, which is the right behavior when the schema couldn't
    be modeled as a typed class.
    """
    if name is None:
        return None
    if available_models is None or name in available_models:
        return name
    return None


def _op_context(
    op: Operation | None, available_models: set[str] | None = None
) -> dict[str, Any] | None:
    """Translate an Operation into the small dict templates need."""
    if op is None:
        return None
    return {
        "method": op.method,
        "response_model": _filter_model_name(op.response_model, available_models),
        "request_model": _filter_model_name(op.request_model, available_models),
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
    return f"{_pascal(ns.name)}NamespaceBase"


def _namespace_module(ns: Namespace) -> str:
    return _snake_case(ns.name)


def _collection_class(coll: Collection) -> str:
    return f"{coll.name}CollectionBase"


def _collection_module(coll: Collection) -> str:
    return _snake_case(coll.name)


def _collection_attr(coll: Collection) -> str:
    return _snake_case(_path_segment(coll.path))


def _resource_class(resource: Resource) -> str:
    return f"{resource.name}ResourceBase"


def _resource_module(resource: Resource) -> str:
    return _snake_case(resource.name)


def _action_class(action: Action) -> str:
    return f"{action.name}ActionBase"


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
