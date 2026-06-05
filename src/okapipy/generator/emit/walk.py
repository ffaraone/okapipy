"""Walk the parsed `APIModel` and emit one templated file per node.

The walker decides class names (`<ContextualPascalCase> + <Suffix>`), module names
(`<snake_case_of_class>` minus the suffix), property names on parents (snake_case
of the path segment), and the path-param introduced by each Resource (computed
by diffing parent collection's path).
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from jinja2 import Environment

from okapipy.generator.templating import render_python, snake_case
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Operation,
    Resource,
    Singleton,
)

Shape = Literal["auto", "models", "dicts"]


@dataclass(frozen=True)
class ChildRef:
    """Reference to a child node — used in template contexts for property emission.

    `docstring` is the fully-formatted triple-quoted block spliced into the
    property accessor. `one_line` and `meta_inline` carry the short bullet
    text used in the *parent class*'s docstring map (the section that lists
    every reachable child); they are unused at the property-accessor site.
    """

    attr: str  # snake_case property name
    class_name: str  # PascalCase class name including the `Base` suffix
    module: str  # snake_case module name (no suffix, no extension)
    factory_attr: str  # dunder-protected ClassVar hook, e.g. `__orders_factory__`
    docstring: str | None = None  # docstring for the property accessor (indent=8)
    one_line: str = ""  # short description used in the parent class's docstring map
    meta_inline: str = (
        ""  # optional inline meta in the bullet (e.g. "`POST /admin/reindex`")
    )


@dataclass(frozen=True)
class _StaticBullet:
    """A non-child bullet for a class-docstring section.

    Used to render entries that don't correspond to a child node — e.g.
    `.first()` / `.count()` on a collection, the inline `for item in
    collection:` iteration hint, or the `.create(body)` line whose
    method/path comes from the collection's create op.
    """

    label: str  # rendered as **`label`**
    one_line: str = ""
    meta_inline: str = ""


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


def emit_tree(
    env: Environment,
    api: APIModel,
    project_context: Mapping[str, Any],
    package_path: str,
    available_models: set[str] | None = None,
    *,
    shape: Shape = "auto",
    mount_relpath: str = "",
) -> dict[str, str]:
    """Render every namespace, collection, resource, and action in `api`.

    `available_models` is the set of top-level identifiers actually emitted in
    `models.py` (computed by introspecting dmcg's output). When provided, model
    references not in the set are dropped from import lines and replaced with
    `None` in `response_model` slots — this prevents `ImportError` for schema
    names dmcg inlined or skipped (primitive aliases, empty objects, etc.).
    Passing `None` disables filtering (used in tests that mock the walker
    directly).

    `shape` selects how body and response types are rendered: `"auto"` admits
    both `Foo` and `dict[str, Any]` arms (today's runtime-switchable client);
    `"models"` types body / return as `Foo` / `Foo | None`; `"dicts"` types
    them as `dict[str, Any]` / `dict[str, Any] | None`.
    """
    out: dict[str, str] = {}
    available = available_models if available_models is not None else None
    # Top-level namespaces.
    for ns in api.namespaces:
        out.update(
            _emit_namespace(
                env, ns, project_context, package_path, available, shape, mount_relpath
            )
        )
    # Top-level collections.
    for coll in api.collections:
        out.update(
            _emit_collection(
                env,
                coll,
                project_context,
                package_path,
                available,
                shape,
                mount_relpath,
            )
        )
    # Top-level singletons.
    for sing in api.singletons:
        out.update(
            _emit_singleton(
                env,
                sing,
                project_context,
                package_path,
                available,
                shape,
                mount_relpath,
            )
        )
    # Top-level actions.
    for action in api.actions:
        out.update(
            _emit_action(
                env,
                action,
                project_context,
                package_path,
                available,
                shape,
                mount_relpath,
            )
        )
    return out


def emit_mount_namespace(
    env: Environment,
    mount_namespace: Namespace,
    project_context: Mapping[str, Any],
    package_path: str,
    available_models: set[str] | None,
    shape: Shape,
    mount_relpath: str,
) -> dict[str, str]:
    """Render a synthetic mount-namespace class at `base/<mount_relpath>__init__.py`.

    `mount_namespace` is the synthetic `Namespace` produced by
    `compose.synthesize_mount_namespace(...)`; the spec's top-level
    children appear as its `namespaces` / `collections` / `singletons` /
    `actions` lists.

    The mount-namespace file sits one directory shallower than the
    per-node files inside the same mount (`base/<mount>/__init__.py` vs
    `base/<mount>/collections/foo.py`), so its `runtime_dots` is one
    level shorter than the standard `runtime_dots(mount_relpath)`.
    """
    if not mount_relpath:
        raise ValueError("emit_mount_namespace requires a non-empty mount_relpath")
    available = available_models if available_models is not None else None
    child_namespaces = [
        ChildRef(
            attr=snake_case(child.name),
            class_name=namespace_class(child),
            module=namespace_module(child),
            factory_attr=factory_attr(snake_case(child.name)),
            docstring=namespace_accessor_docstring(child),
            one_line=node_one_line(
                child.summary,
                child.description,
                fallback=f"Namespace `{child.name}`.",
            ),
        )
        for child in mount_namespace.namespaces
    ]
    child_collections = [
        ChildRef(
            attr=collection_attr(coll),
            class_name=collection_class(coll),
            module=collection_module(coll),
            factory_attr=factory_attr(collection_attr(coll)),
            docstring=collection_property_docstring(coll),
            one_line=collection_one_line(coll),
        )
        for coll in mount_namespace.collections
    ]
    child_singletons = [
        ChildRef(
            attr=singleton_attr(sing),
            class_name=singleton_class(sing),
            module=singleton_module(sing),
            factory_attr=factory_attr(singleton_attr(sing)),
            docstring=singleton_accessor_docstring(sing),
            one_line=singleton_one_line(sing),
        )
        for sing in mount_namespace.singletons
    ]
    child_actions = [
        ChildRef(
            attr=action_attr(action),
            class_name=action_class(action),
            module=action_module(action),
            factory_attr=factory_attr(action_attr(action)),
            docstring=action_accessor_docstring(action),
            one_line=action_one_line(action),
            meta_inline=action_meta_inline(action),
        )
        for action in mount_namespace.actions
    ]
    _ = available  # mount namespace itself imports no models; children do.
    _ = shape
    # The mount __init__.py is one package shallower than per-node files
    # under the same mount, so it needs one fewer dot prefix to reach
    # base-level shared modules (`client`, `exceptions`, …).
    mount_init_runtime_dots = "." * (mount_relpath.count("/") + 1)
    from okapipy.generator.compose import mount_class_name

    ctx = {
        **project_context,
        "class_name": mount_class_name(mount_namespace),
        "child_namespaces": child_namespaces,
        "child_collections": child_collections,
        "child_singletons": child_singletons,
        "child_actions": child_actions,
        "runtime_dots": mount_init_runtime_dots,
        "is_mount_root": True,
        "class_docstring": _build_namespace_class_docstring(
            mount_namespace,
            child_namespaces=child_namespaces,
            child_collections=child_collections,
            child_singletons=child_singletons,
            child_actions=child_actions,
        ),
    }
    return {
        f"src/{package_path}/base/{mount_relpath}__init__.py": render_python(
            env, "package/namespace.py.jinja", ctx
        ),
    }


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
        cls = namespace_class(ns)
        import_lines.append(
            f"from .namespaces.{namespace_module(ns)} import {cls}, Async{cls}"
        )
        public_names.append(cls)
        public_names.append(f"Async{cls}")
    for coll in api.collections:
        cls = collection_class(coll)
        import_lines.append(
            f"from .collections.{collection_module(coll)} import {cls}, Async{cls}"
        )
        public_names.append(cls)
        public_names.append(f"Async{cls}")
    for sing in api.singletons:
        cls = singleton_class(sing)
        import_lines.append(
            f"from .singletons.{singleton_module(sing)} import {cls}, Async{cls}"
        )
        public_names.append(cls)
        public_names.append(f"Async{cls}")
    for action in api.actions:
        cls = action_class(action)
        import_lines.append(
            f"from .actions.{action_module(action)} import {cls}, Async{cls}"
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
    shape: Shape,
    mount_relpath: str = "",
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_namespaces: list[ChildRef] = [
        ChildRef(
            attr=snake_case(child.name),
            class_name=namespace_class(child),
            module=namespace_module(child),
            factory_attr=factory_attr(snake_case(child.name)),
            docstring=namespace_accessor_docstring(child),
            one_line=node_one_line(
                child.summary,
                child.description,
                fallback=f"Namespace `{child.name}`.",
            ),
        )
        for child in ns.namespaces
    ]
    child_collections: list[ChildRef] = [
        ChildRef(
            attr=collection_attr(coll),
            class_name=collection_class(coll),
            module=collection_module(coll),
            factory_attr=factory_attr(collection_attr(coll)),
            docstring=collection_property_docstring(coll),
            one_line=collection_one_line(coll),
        )
        for coll in ns.collections
    ]
    child_singletons: list[ChildRef] = [
        ChildRef(
            attr=singleton_attr(sing),
            class_name=singleton_class(sing),
            module=singleton_module(sing),
            factory_attr=factory_attr(singleton_attr(sing)),
            docstring=singleton_accessor_docstring(sing),
            one_line=singleton_one_line(sing),
        )
        for sing in ns.singletons
    ]
    child_actions: list[ChildRef] = [
        ChildRef(
            attr=action_attr(action),
            class_name=action_class(action),
            module=action_module(action),
            factory_attr=factory_attr(action_attr(action)),
            docstring=action_accessor_docstring(action),
            one_line=action_one_line(action),
            meta_inline=action_meta_inline(action),
        )
        for action in ns.actions
    ]
    ctx = {
        **project_context,
        "class_name": namespace_class(ns),
        "child_namespaces": child_namespaces,
        "child_collections": child_collections,
        "child_singletons": child_singletons,
        "child_actions": child_actions,
        "runtime_dots": runtime_dots(mount_relpath),
        "is_mount_root": False,
        "class_docstring": _build_namespace_class_docstring(
            ns,
            child_namespaces=child_namespaces,
            child_collections=child_collections,
            child_singletons=child_singletons,
            child_actions=child_actions,
        ),
    }
    out[
        f"src/{package_path}/base/{mount_relpath}namespaces/{namespace_module(ns)}.py"
    ] = render_python(env, "package/namespace.py.jinja", ctx)
    for child in ns.namespaces:
        out.update(
            _emit_namespace(
                env,
                child,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for coll in ns.collections:
        out.update(
            _emit_collection(
                env,
                coll,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for sing in ns.singletons:
        out.update(
            _emit_singleton(
                env,
                sing,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for action in ns.actions:
        out.update(
            _emit_action(
                env,
                action,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
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
    shape: Shape,
    mount_relpath: str = "",
) -> dict[str, str]:
    out: dict[str, str] = {}
    resource_ref: dict[str, str] | None = None
    resource_bullet: ChildRef | None = None
    if coll.resource is not None:
        id_param = _new_path_param(coll.path, coll.resource.path)
        res_class = resource_class(coll.resource)
        resource_ref = {
            "class_name": res_class,
            "module": resource_module(coll.resource),
            "id_param": id_param,
            "factory_attr": factory_attr("resource"),
            "docstring": getitem_accessor_docstring(id_param=id_param),
        }
        resource_bullet = ChildRef(
            attr=f"collection[{id_param}]",
            class_name=res_class,
            module=resource_module(coll.resource),
            factory_attr=factory_attr("resource"),
            one_line=node_one_line(
                coll.resource.summary,
                coll.resource.description,
                fallback=f"One item by `{id_param}`.",
            ),
        )
    actions_dicts = [
        {
            "attr": action_attr(action),
            "class_name": action_class(action),
            "module": action_module(action),
            "factory_attr": factory_attr(action_attr(action)),
            "docstring": action_accessor_docstring(action),
        }
        for action in coll.actions
    ]
    action_bullets = [
        ChildRef(
            attr=action_attr(action),
            class_name=action_class(action),
            module=action_module(action),
            factory_attr=factory_attr(action_attr(action)),
            one_line=action_one_line(action),
            meta_inline=action_meta_inline(action),
        )
        for action in coll.actions
    ]
    child_singletons = [
        ChildRef(
            attr=singleton_attr(sing),
            class_name=singleton_class(sing),
            module=singleton_module(sing),
            factory_attr=factory_attr(singleton_attr(sing)),
            docstring=singleton_accessor_docstring(sing),
            one_line=singleton_one_line(sing),
        )
        for sing in coll.singletons
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
    create_op_ctx = _op_context(create_op, available_models, shape)
    class_doc = _build_collection_class_docstring(
        coll,
        resource_ref=resource_bullet,
        actions=action_bullets,
        child_singletons=child_singletons,
        create_op=create_op,
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
        "class_name": collection_class(coll),
        "path_template": coll.path,
        "resource": resource_ref,
        "actions": actions_dicts,
        "child_singletons": child_singletons,
        "create_op": create_op_ctx,
        "fetch_response_model": (
            _filter_model_name(fetch_op.response_model, available_models)
            if fetch_op is not None
            else None
        ),
        "fetch_item_model": fetch_item_model,
        "item_type": _iterator_item_type(fetch_item_model, shape),
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
        "runtime_dots": runtime_dots(mount_relpath),
    }
    out[
        f"src/{package_path}/base/{mount_relpath}collections/{collection_module(coll)}.py"
    ] = render_python(env, "package/collection.py.jinja", ctx)
    if coll.resource is not None:
        out.update(
            _emit_resource(
                env,
                coll,
                coll.resource,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for action in coll.actions:
        out.update(
            _emit_action(
                env,
                action,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for sing in coll.singletons:
        out.update(
            _emit_singleton(
                env,
                sing,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
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
    shape: Shape,
    mount_relpath: str = "",
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_collections = [
        ChildRef(
            attr=collection_attr(coll),
            class_name=collection_class(coll),
            module=collection_module(coll),
            factory_attr=factory_attr(collection_attr(coll)),
            docstring=collection_property_docstring(coll),
            one_line=collection_one_line(coll),
        )
        for coll in resource.collections
    ]
    child_singletons = [
        ChildRef(
            attr=singleton_attr(sing),
            class_name=singleton_class(sing),
            module=singleton_module(sing),
            factory_attr=factory_attr(singleton_attr(sing)),
            docstring=singleton_accessor_docstring(sing),
            one_line=singleton_one_line(sing),
        )
        for sing in resource.singletons
    ]
    actions = [
        ChildRef(
            attr=action_attr(action),
            class_name=action_class(action),
            module=action_module(action),
            factory_attr=factory_attr(action_attr(action)),
            docstring=action_accessor_docstring(action),
            one_line=action_one_line(action),
            meta_inline=action_meta_inline(action),
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
        "class_name": resource_class(resource),
        "path_template": resource.path,
        "retrieve_op": _op_context(resource.retrieve, available_models, shape),
        "update_op": _op_context(resource.update, available_models, shape),
        "patch_op": _op_context(resource.partial_update, available_models, shape),
        "delete_op": _op_context(resource.delete, available_models, shape),
        "child_collections": child_collections,
        "child_singletons": child_singletons,
        "actions": actions,
        "model_imports": model_imports,
        "class_docstring": _build_resource_class_docstring(
            resource,
            child_collections=child_collections,
            child_singletons=child_singletons,
            actions=actions,
        ),
        "retrieve_docstring": _op_doc(resource.retrieve),
        "update_docstring": _op_doc(resource.update, " (full replacement)"),
        "patch_docstring": _op_doc(resource.partial_update, " (partial update)"),
        "delete_docstring": _op_doc(resource.delete),
        "runtime_dots": runtime_dots(mount_relpath),
    }
    out[
        f"src/{package_path}/base/{mount_relpath}resources/{resource_module(resource)}.py"
    ] = render_python(env, "package/resource.py.jinja", ctx)
    for coll in resource.collections:
        out.update(
            _emit_collection(
                env,
                coll,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for sing in resource.singletons:
        out.update(
            _emit_singleton(
                env,
                sing,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for action in resource.actions:
        out.update(
            _emit_action(
                env,
                action,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    _ = parent_coll  # parent context kept for future use (e.g. type hints)
    return out


# --------------------------------------------------------------------------- #
# Singleton                                                                   #
# --------------------------------------------------------------------------- #


def _emit_singleton(
    env: Environment,
    singleton: Singleton,
    project_context: Mapping[str, Any],
    package_path: str,
    available_models: set[str] | None,
    shape: Shape,
    mount_relpath: str = "",
) -> dict[str, str]:
    out: dict[str, str] = {}
    child_collections = [
        ChildRef(
            attr=collection_attr(coll),
            class_name=collection_class(coll),
            module=collection_module(coll),
            factory_attr=factory_attr(collection_attr(coll)),
            docstring=collection_property_docstring(coll),
            one_line=collection_one_line(coll),
        )
        for coll in singleton.collections
    ]
    child_singletons = [
        ChildRef(
            attr=singleton_attr(sub),
            class_name=singleton_class(sub),
            module=singleton_module(sub),
            factory_attr=factory_attr(singleton_attr(sub)),
            docstring=singleton_accessor_docstring(sub),
            one_line=singleton_one_line(sub),
        )
        for sub in singleton.singletons
    ]
    actions = [
        ChildRef(
            attr=action_attr(action),
            class_name=action_class(action),
            module=action_module(action),
            factory_attr=factory_attr(action_attr(action)),
            docstring=action_accessor_docstring(action),
            one_line=action_one_line(action),
            meta_inline=action_meta_inline(action),
        )
        for action in singleton.actions
    ]
    model_imports = sorted(
        _collect_model_names(
            [
                singleton.retrieve,
                singleton.update,
                singleton.partial_update,
                singleton.delete,
            ],
            available_models,
        )
    )

    def _op_doc(op: Operation | None, suffix: str = "") -> str | None:
        if op is None:
            return None
        fallback = f"`{op.method} {singleton.path}`{suffix}."
        return build_docstring(op.summary, op.description, fallback=fallback, indent=8)

    ctx = {
        **project_context,
        "class_name": singleton_class(singleton),
        "path_template": singleton.path,
        "retrieve_op": _op_context(singleton.retrieve, available_models, shape),
        "update_op": _op_context(singleton.update, available_models, shape),
        "patch_op": _op_context(singleton.partial_update, available_models, shape),
        "delete_op": _op_context(singleton.delete, available_models, shape),
        "child_collections": child_collections,
        "child_singletons": child_singletons,
        "actions": actions,
        "model_imports": model_imports,
        "class_docstring": _build_singleton_class_docstring(
            singleton,
            child_collections=child_collections,
            child_singletons=child_singletons,
            actions=actions,
        ),
        "retrieve_docstring": _op_doc(singleton.retrieve),
        "update_docstring": _op_doc(singleton.update, " (full replacement)"),
        "patch_docstring": _op_doc(singleton.partial_update, " (partial update)"),
        "delete_docstring": _op_doc(singleton.delete),
        "runtime_dots": runtime_dots(mount_relpath),
    }
    out[
        f"src/{package_path}/base/{mount_relpath}singletons/{singleton_module(singleton)}.py"
    ] = render_python(env, "package/singleton.py.jinja", ctx)
    for coll in singleton.collections:
        out.update(
            _emit_collection(
                env,
                coll,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for sub in singleton.singletons:
        out.update(
            _emit_singleton(
                env,
                sub,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
    for action in singleton.actions:
        out.update(
            _emit_action(
                env,
                action,
                project_context,
                package_path,
                available_models,
                shape,
                mount_relpath,
            )
        )
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
    shape: Shape,
    mount_relpath: str = "",
) -> dict[str, str]:
    operations = [_op_context(op, available_models, shape) for op in action.operations]
    operations = [op for op in operations if op is not None]
    single_op = operations[0] if len(operations) == 1 else None
    model_imports = sorted(_collect_model_names(action.operations, available_models))
    # Action docstrings: when the action has a single HTTP method, the class
    # docstring and that method's docstring share one source — the action's
    # summary/description — because the class and the method describe the same
    # thing. When the action has multiple methods, the class docstring lists
    # every operation and each method gets its own docstring from its own
    # summary/description.
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
        "class_name": action_class(action),
        "path_template": action.path,
        "operations": operations,
        "single_op": single_op,
        "model_imports": model_imports,
        "class_docstring": class_doc,
        "single_op_docstring": single_op_docstring,
        "op_docstrings": op_docstrings,
        "runtime_dots": runtime_dots(mount_relpath),
    }
    return {
        f"src/{package_path}/base/{mount_relpath}actions/{action_module(action)}.py": render_python(
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
    """Build the docstring for a property that exposes a `Collection`.

    The accessor (e.g. `Admin.accounts`, `Order.lines`, `client.orders`)
    inherits the collection's `fetch` operation docs so the call site shows
    the collection's purpose without forcing the user to navigate into the
    collection class. When fetch has no documentation, fall back to the
    collection's own summary/description, and finally to a structural
    string identifying the path.
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
    """Format an action class docstring: single-op uses the op's text, multi-op lists them.

    Multi-op actions render an `#### Operations` section with one bullet per
    HTTP verb so the IDE tooltip reads as a self-contained map. Single-op
    actions reuse the operation's own summary/description because the class
    and the only method are documenting the same thing.
    """
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
    header.append("#### Operations")
    header.append("")
    for op in action.operations:
        summary = (op.summary or "").strip() or "(no summary)"
        header.append(f"- `{op.method} {action.path}` — {summary}")
    return _build_docstring_from_body("\n".join(header), indent)


def build_client_class_docstring(
    *,
    project_name: str,
    project_version: str | None,
    sync: bool,
    top_namespaces: Sequence[ChildRef],
    top_collections: Sequence[ChildRef],
    top_singletons: Sequence[ChildRef],
    top_actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Render the IDE-facing class docstring for the client base class.

    The body opens with a one-line lead identifying the project and shape,
    then lists each top-level child kind that is populated. Sections are
    omitted when their child list is empty so a tiny spec doesn't produce a
    docstring full of empty headers.

    `sync=True` produces the `<Client>Base` flavor; `sync=False` produces
    the async sibling text. Both flavors share the same map — only the lead
    differs.
    """
    if sync:
        title = f"HTTP client for `{project_name}`"
    else:
        title = f"Asynchronous HTTP client for `{project_name}`"
    if project_version:
        title += f" (v{project_version})."
    else:
        title += "."
    lead_lines = [
        title,
        "",
        "Construct with `base_url=...`. Configure pagination, filter, and sort",
        "strategies via the matching keyword arguments.",
    ]
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    if top_collections:
        sections.append(("Top-level collections", top_collections))
    if top_singletons:
        sections.append(("Top-level singletons", top_singletons))
    if top_namespaces:
        sections.append(("Top-level namespaces", top_namespaces))
    if top_actions:
        sections.append(("Top-level actions", top_actions))
    body = _compose_class_doc_body(lead="\n".join(lead_lines), sections=sections)
    return _build_docstring_from_body(body, indent)


def namespace_accessor_docstring(ns: Namespace, indent: int = 8) -> str:
    """Return the property-accessor docstring for a namespace (always non-None).

    A short one-liner so the IDE popup stays compact. We deliberately do
    not name the namespace class here because the same string is reused
    for the sync and async accessors; pinning a class name would mislead
    one of the two readers.
    """
    summary = node_one_line(
        ns.summary, ns.description, fallback=f"Namespace `{ns.name}`."
    )
    return _build_docstring_from_body(summary, indent)


def singleton_accessor_docstring(singleton: Singleton, indent: int = 8) -> str:
    """Return the property-accessor docstring for a singleton (always non-None)."""
    fallback = f"Singleton at `{singleton.path}`."
    candidate_summary = singleton.summary
    candidate_description = singleton.description
    if (
        not (candidate_summary or candidate_description)
        and singleton.retrieve is not None
    ):
        candidate_summary = singleton.retrieve.summary
        candidate_description = singleton.retrieve.description
    summary = node_one_line(candidate_summary, candidate_description, fallback=fallback)
    return _build_docstring_from_body(summary, indent)


def action_accessor_docstring(action: Action, indent: int = 8) -> str:
    """Return the property-accessor docstring for an action (always non-None)."""
    candidate_summary = action.summary
    candidate_description = action.description
    if not (candidate_summary or candidate_description) and action.operations:
        candidate_summary = action.operations[0].summary
        candidate_description = action.operations[0].description
    fallback = f"Action at `{action.path}`."
    summary = node_one_line(candidate_summary, candidate_description, fallback=fallback)
    if len(action.operations) == 1:
        op = action.operations[0]
        body = f"`{op.method} {action.path}`. {summary}"
    else:
        body = summary
    return _build_docstring_from_body(body, indent)


def getitem_accessor_docstring(*, id_param: str, indent: int = 8) -> str:
    """Return the docstring for `Collection.__getitem__` (always non-None).

    Indexing is request-free — the resource is constructed lazily and
    only issues a request when one of its CRUD methods is called. The
    docstring is sync/async-agnostic; the actual return type comes from
    the property annotation, which already encodes the correct sibling.
    """
    body = (
        f"Address one item by `{id_param}`. No HTTP call until a CRUD "
        f"method runs on the returned resource."
    )
    return _build_docstring_from_body(body, indent)


def _build_docstring_from_body(
    body: str, indent: int, max_line_length: int = 100
) -> str:
    """Wrap `body` in a triple-quoted block at the given indent level.

    Long prose paragraphs (e.g. an OpenAPI `description` spliced verbatim
    into the body) are reflowed to fit `max_line_length` once the leading
    pad and `\"\"\"` overhead are accounted for. Markdown bullets keep a
    hanging two-space indent on continuation lines so they still render
    as a single bullet; section headings (`#### …`) and pre-broken lines
    in the body are preserved verbatim.
    """
    body = body.replace('"""', "'''")
    pad = " " * indent
    # Subtract 6 to fit the worst case `pad + """body"""` form: ruff's
    # docstring formatter collapses a fitting multi-line block onto one
    # line, and the closing triple-quote then adds 3 chars on top of the
    # opening 3. Wrapping more conservatively keeps the post-format step
    # honest with its own line-length lint.
    wrap_width = max(20, max_line_length - indent - 6)
    body = _wrap_docstring_body(body, wrap_width)
    lines = body.split("\n")
    if len(lines) == 1 and len(lines[0]) + indent + 6 <= max_line_length:
        return f'{pad}"""{lines[0]}"""'
    first = lines[0]
    rest = lines[1:]
    out_lines = [f'{pad}"""{first}']
    for line in rest:
        out_lines.append(pad + line if line.strip() else "")
    out_lines.append(f'{pad}"""')
    return "\n".join(out_lines)


def _wrap_docstring_body(body: str, width: int) -> str:
    """Reflow `body` so each line fits within `width` columns.

    Walks the body line by line. Consecutive non-empty, non-structural
    lines are grouped into a single paragraph and rewrapped together.
    Bullet lines (starting with `- `) are wrapped with a hanging
    two-space indent so the bullet structure survives. Section headings
    (`#### …`) and blank lines are preserved verbatim.
    """
    out: list[str] = []
    paragraph: list[str] = []

    def _flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(line.strip() for line in paragraph if line.strip())
        if text:
            out.append(
                textwrap.fill(
                    text,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
        paragraph.clear()

    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            _flush_paragraph()
            out.append("")
            continue
        if stripped.startswith("####"):
            _flush_paragraph()
            out.append(line)
            continue
        if stripped.startswith("- "):
            _flush_paragraph()
            out.append(
                textwrap.fill(
                    line,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                    subsequent_indent="  ",
                )
            )
            continue
        paragraph.append(line)
    _flush_paragraph()

    return "\n".join(out)


def _build_namespace_class_docstring(
    ns: Namespace,
    *,
    child_namespaces: Sequence[ChildRef],
    child_collections: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    child_actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Lead paragraph from `ns.summary`/`ns.description`, then a map of children."""
    fallback = f"Namespace router for `{ns.name}`."
    lead = _lead_paragraph(ns.summary, ns.description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    if child_namespaces:
        sections.append(("Sub-namespaces", child_namespaces))
    if child_collections:
        sections.append(("Collections", child_collections))
    if child_singletons:
        sections.append(("Singletons", child_singletons))
    if child_actions:
        sections.append(("Actions", child_actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def _build_collection_class_docstring(
    coll: Collection,
    *,
    resource_ref: ChildRef | None,
    actions: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    create_op: Operation | None,
    indent: int = 4,
) -> str:
    """Compose the collection docstring from fetch op + item / ops / sub-singletons / actions.

    `Operations on the collection` always lists the standard query helpers
    (`first`, `count`, `exists`, `get_page`, iteration); the `create(body)`
    bullet is added only when the parser populated `Collection.create`.
    `Item access` is omitted when there is no resource child. `Sub-singletons`
    appears when the collection hosts aggregate-view singletons such as
    `/orders/stats`.
    """
    fallback = f"Collection at `{coll.path}`."
    if coll.fetch is not None:
        lead = _lead_paragraph(coll.fetch.summary, coll.fetch.description, fallback)
    else:
        lead = _lead_paragraph(coll.summary, coll.description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    if resource_ref is not None:
        sections.append(("Item access", [resource_ref]))
    sections.append(
        ("Operations on the collection", _collection_operation_bullets(coll, create_op))
    )
    if child_singletons:
        sections.append(("Sub-singletons", child_singletons))
    if actions:
        sections.append(("Actions", actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def _build_resource_class_docstring(
    resource: Resource,
    *,
    child_collections: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Lead from `resource.summary`/`description`, then CRUD / sub-trees / actions."""
    fallback = f"Resource at `{resource.path}`."
    lead = _lead_paragraph(resource.summary, resource.description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    crud = _crud_bullets(
        path=resource.path,
        retrieve=resource.retrieve,
        update=resource.update,
        partial_update=resource.partial_update,
        delete=resource.delete,
    )
    if crud:
        sections.append(("Operations", crud))
    if child_collections:
        sections.append(("Sub-collections", child_collections))
    if child_singletons:
        sections.append(("Sub-singletons", child_singletons))
    if actions:
        sections.append(("Actions", actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def _build_singleton_class_docstring(
    singleton: Singleton,
    *,
    child_collections: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Same shape as the resource builder, minus `[id]` indexing."""
    fallback = f"Singleton at `{singleton.path}`."
    candidate_summary = singleton.summary
    candidate_description = singleton.description
    if (
        not (candidate_summary or candidate_description)
        and singleton.retrieve is not None
    ):
        candidate_summary = singleton.retrieve.summary
        candidate_description = singleton.retrieve.description
    lead = _lead_paragraph(candidate_summary, candidate_description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    crud = _crud_bullets(
        path=singleton.path,
        retrieve=singleton.retrieve,
        update=singleton.update,
        partial_update=singleton.partial_update,
        delete=singleton.delete,
    )
    if crud:
        sections.append(("Operations", crud))
    if child_collections:
        sections.append(("Sub-collections", child_collections))
    if child_singletons:
        sections.append(("Sub-singletons", child_singletons))
    if actions:
        sections.append(("Actions", actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def _compose_class_doc_body(
    *,
    lead: str,
    sections: Sequence[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]],
) -> str:
    """Stitch a class-docstring body: lead paragraph + non-empty markdown sections."""
    chunks: list[str] = [lead.rstrip()]
    for title, items in sections:
        if not items:
            continue
        chunks.append("")
        chunks.append(f"#### {title}")
        chunks.append("")
        for item in items:
            chunks.append(_render_bullet(item))
    return "\n".join(chunks)


def _render_bullet(entry: ChildRef | _StaticBullet) -> str:
    """Format a single section bullet in markdown.

    Both `ChildRef` and `_StaticBullet` may carry an optional `meta_inline`
    (rendered as backticked code, e.g. `` `POST /admin/reindex` ``) and a
    `one_line` (the short prose). When both are present we join them with a
    period; when neither is present we emit just the head.
    """
    if isinstance(entry, ChildRef):
        head = f"- **`{entry.attr}`** → `{entry.class_name}`"
    else:
        head = f"- **`{entry.label}`**"
    bits = [piece for piece in (entry.meta_inline, entry.one_line) if piece]
    if not bits:
        return head
    rhs = ". ".join(bits)
    if not rhs.endswith((".", "?", "!")):
        rhs += "."
    return f"{head} — {rhs}"


def _lead_paragraph(summary: str | None, description: str | None, fallback: str) -> str:
    """Lead-paragraph composition shared by every class-docstring builder.

    Same precedence as `build_docstring` (summary, then description, then
    fallback) but always yields a non-empty string and never includes
    section headers — the caller appends those.
    """
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if description and description.strip():
        parts.append(description.strip())
    if not parts:
        return fallback
    return "\n\n".join(parts)


def node_one_line(
    summary: str | None,
    description: str | None,
    *,
    fallback: str,
) -> str:
    """Return a single-line summary suitable for a bullet's `one_line` slot.

    Picks the first non-empty source (summary, description, fallback),
    keeps only its first line, and trims to the first sentence-terminator
    (`.`, `?`, `!`) when one occurs in the first ~120 characters. The
    result is *not* punctuated automatically — the bullet renderer adds a
    period only when none of the source text already ends with one.
    """
    raw = (summary or "").strip() or (description or "").strip() or fallback.strip()
    first_line = raw.split("\n", 1)[0].strip()
    if not first_line:
        return fallback.strip()
    for terminator in (". ", "? ", "! "):
        idx = first_line.find(terminator)
        if idx != -1 and idx < 120:
            return first_line[: idx + 1]
    if len(first_line) > 200:
        return first_line[:200].rstrip() + "…"
    return first_line


def _collection_operation_bullets(
    coll: Collection, create_op: Operation | None
) -> list[_StaticBullet]:
    """Build the `#### Operations on the collection` bullets.

    Lists the standard query helpers plus a `.create(body)` line when the
    parser populated a create operation. Iteration is folded in as a
    structural hint rather than a method call so users see the `for`
    pattern at a glance.
    """
    bullets: list[_StaticBullet] = []
    if create_op is not None:
        bullets.append(
            _StaticBullet(
                label=".create(body)",
                meta_inline=f"`{create_op.method} {coll.path}`",
                one_line=node_one_line(
                    create_op.summary,
                    create_op.description,
                    fallback="Create a new item.",
                ),
            )
        )
    bullets.extend(
        [
            _StaticBullet(
                label=".first()", one_line="Return the first item, or `None`."
            ),
            _StaticBullet(
                label=".count()",
                one_line=(
                    "Return the total count via the configured pagination strategy"
                ),
            ),
            _StaticBullet(label=".exists()", one_line="Equivalent to `count() > 0`"),
            _StaticBullet(
                label=".get_page(n)",
                one_line=(
                    "Fetch a single 0-indexed page (offset/page-number strategies only)"
                ),
            ),
            _StaticBullet(
                label="for item in collection: ...",
                one_line="Paginated iteration",
            ),
        ]
    )
    return bullets


def _crud_bullets(
    *,
    path: str,
    retrieve: Operation | None,
    update: Operation | None,
    partial_update: Operation | None,
    delete: Operation | None,
) -> list[_StaticBullet]:
    """Bullets for the CRUD methods a Resource or Singleton actually exposes.

    A method that the spec did not declare is silently dropped — listing it
    in the docstring would surface a method that doesn't exist on the
    class.
    """
    bullets: list[_StaticBullet] = []
    pairings: tuple[tuple[str, Operation | None, str], ...] = (
        (".retrieve()", retrieve, "Fetch the item."),
        (".update(body)", update, "Replace the item."),
        (".patch(body)", partial_update, "Modify selected fields."),
        (".delete()", delete, "Delete the item."),
    )
    for label, op, fallback in pairings:
        if op is None:
            continue
        bullets.append(
            _StaticBullet(
                label=label,
                meta_inline=f"`{op.method} {path}`",
                one_line=node_one_line(op.summary, op.description, fallback=fallback),
            )
        )
    return bullets


def collection_one_line(coll: Collection) -> str:
    """One-liner for a collection bullet — falls back to the fetch op's summary."""
    if coll.summary or coll.description:
        return node_one_line(
            coll.summary,
            coll.description,
            fallback=f"Collection at `{coll.path}`.",
        )
    if coll.fetch is not None:
        return node_one_line(
            coll.fetch.summary,
            coll.fetch.description,
            fallback=f"Collection at `{coll.path}`.",
        )
    return f"Collection at `{coll.path}`."


def singleton_one_line(singleton: Singleton) -> str:
    """One-liner for a singleton bullet — falls back to the retrieve op's summary."""
    if singleton.summary or singleton.description:
        return node_one_line(
            singleton.summary,
            singleton.description,
            fallback=f"Singleton at `{singleton.path}`.",
        )
    if singleton.retrieve is not None:
        return node_one_line(
            singleton.retrieve.summary,
            singleton.retrieve.description,
            fallback=f"Singleton at `{singleton.path}`.",
        )
    return f"Singleton at `{singleton.path}`."


def action_meta_inline(action: Action) -> str:
    """Inline meta string for an action when it appears as a bullet under a parent."""
    if len(action.operations) == 1:
        op = action.operations[0]
        return f"`{op.method} {action.path}`"
    if action.operations:
        return f"`{action.path}` (multiple ops)"
    return f"`{action.path}`"


def action_one_line(action: Action) -> str:
    """One-liner for an action bullet — falls back to the only op's summary."""
    if action.summary or action.description:
        return node_one_line(
            action.summary, action.description, fallback=f"Action at `{action.path}`."
        )
    if len(action.operations) == 1:
        op = action.operations[0]
        return node_one_line(
            op.summary, op.description, fallback=f"Action at `{action.path}`."
        )
    return f"Action at `{action.path}`."


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
        names.update(op.request_model_members)
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

    dmcg strips non-alphanumeric characters from `$ref` schema names — e.g.
    `LimitOffsetPage_OrganizationRead_` becomes `LimitOffsetPageOrganizationRead`.
    The parser, on the other hand, copies the original ref segment verbatim. We
    apply the same normalization as a fallback so generic-style names recovered
    by the parser still resolve to the class dmcg actually emitted.
    """
    if name is None:
        return None
    if available_models is None:
        return name
    if name in available_models:
        return name
    sanitized = _dmcg_class_name(name)
    if sanitized in available_models:
        return sanitized
    return None


_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def _dmcg_class_name(name: str) -> str:
    """PascalCase a `$ref` schema name the way `datamodel-code-generator` does.

    Splits on every non-alphanumeric run, drops empty parts, and capitalizes
    the first letter of each surviving fragment while preserving the rest of
    its casing. `LimitOffsetPage_OrganizationRead_` → `LimitOffsetPageOrganizationRead`.
    """
    parts = _NON_ALNUM.split(name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _op_context(
    op: Operation | None,
    available_models: set[str] | None = None,
    shape: Shape = "auto",
) -> dict[str, Any] | None:
    """Translate an Operation into the small dict templates need."""
    if op is None:
        return None
    request_model = _filter_model_name(op.request_model, available_models)
    members = [
        name
        for name in op.request_model_members
        if available_models is None or name in available_models
    ]
    has_body = bool(op.request_model) or bool(op.request_model_members)
    response_model = _filter_model_name(op.response_model, available_models)
    return {
        "method": op.method,
        "response_model": response_model,
        "request_model": request_model,
        "request_model_members": members,
        "body_type": _body_type(request_model, members, shape),
        "response_type": _response_type(response_model, shape),
        "has_body": has_body,
        "pagination_supported": op.pagination_supported,
        "filter_supported": op.filter_supported,
        "sort_supported": op.sort_supported,
    }


def _response_type(response_model: str | None, shape: Shape) -> str:
    """Render the Python return type for an operation that calls `from_response`.

    The result depends on the configured `shape`:

    * `auto` admits both arms — `Foo | dict[str, Any] | None` when a schema
      name was recovered, `dict[str, Any] | None` otherwise.
    * `models` types known schemas as `Foo | None`; unrecovered schemas fall
      back to `dict[str, Any] | None` because the runtime returns raw JSON
      when no class was emitted for the response.
    * `dicts` always types the return as `dict[str, Any] | None` — the client
      never validates, so the model arm is unreachable.
    """
    if shape == "dicts":
        return "dict[str, Any] | None"
    if shape == "models":
        if response_model:
            return f"{response_model} | None"
        return "dict[str, Any] | None"
    if response_model:
        return f"{response_model} | dict[str, Any] | None"
    return "dict[str, Any] | None"


def _iterator_item_type(item_model: str | None, shape: Shape) -> str:
    """Render the per-item type yielded by a collection iterator.

    Distinct from `_response_type`: a collection iterator signals exhaustion
    by raising `StopIteration` / `StopAsyncIteration`, never by yielding
    `None`. Each yielded value is either a parsed model instance, a raw JSON
    object, or both depending on `shape`; never `None`. The collection's
    `first()` accessor is the only place where `None` is a legitimate return
    (no items at all); the template adds `| None` there.
    """
    if shape == "dicts":
        return "dict[str, Any]"
    if shape == "models":
        return item_model if item_model else "dict[str, Any]"
    if item_model:
        return f"{item_model} | dict[str, Any]"
    return "dict[str, Any]"


def _body_type(request_model: str | None, members: Sequence[str], shape: Shape) -> str:
    """Render the Python type expression for the operation's `body` parameter.

    The result depends on the configured `shape`:

    * `auto` admits a plain `dict[str, Any]` alongside any typed model(s) so
      callers may pass a raw payload without satisfying the Pydantic class —
      the runtime serializes models or dicts interchangeably. Multiple
      `anyOf` members produce `A | B | dict[str, Any]`; a single class
      produces `A | dict[str, Any]`; an empty/filtered request schema falls
      back to `Any`.
    * `models` drops the dict arm — body must be the recovered model
      (or anyOf union); falls back to `Any` when no schema name was
      recovered.
    * `dicts` types every body as `dict[str, Any]` regardless of any
      recovered schema name (and `Any` when no schema was recovered, to
      keep callers unconstrained).
    """
    if shape == "dicts":
        if request_model or members:
            return "dict[str, Any]"
        return "Any"
    if shape == "models":
        if members:
            return " | ".join(members)
        if request_model:
            return request_model
        return "Any"
    if members:
        return " | ".join([*members, "dict[str, Any]"])
    if request_model:
        return f"{request_model} | dict[str, Any]"
    return "Any"


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


def _pascal(value: str) -> str:
    """Local PascalCase helper used by namespace class naming."""
    return "".join(part.capitalize() for part in snake_case(value).split("_") if part)


# Stop the unused-import linter from complaining about `Iterable`; keep the
# alias in case a future caller switches the public API to an iterator return.
_ = Iterable
