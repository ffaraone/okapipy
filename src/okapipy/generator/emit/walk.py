"""Walk the parsed `APIModel` and emit one templated file per node.

The walker decides class names, module names, property names, and the
path-param introduced by each Resource (by diffing the parent collection's
path) — then hands the result to the right Jinja template. Helpers for
naming, docstring composition, and operation/type context live in sibling
modules (`names`, `docstrings`, `op_context`); this file is just the
recursion.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jinja2 import Environment

from okapipy.generator.emit.docstrings import (
    ChildRef,
    action_accessor_docstring,
    action_meta_inline,
    action_one_line,
    build_action_docstring,
    build_collection_class_docstring,
    build_docstring,
    build_namespace_class_docstring,
    build_resource_class_docstring,
    build_singleton_class_docstring,
    collection_one_line,
    collection_property_docstring,
    getitem_accessor_docstring,
    namespace_accessor_docstring,
    node_one_line,
    singleton_accessor_docstring,
    singleton_one_line,
)
from okapipy.generator.emit.names import (
    action_attr,
    action_class,
    action_module,
    collection_attr,
    collection_class,
    collection_module,
    factory_attr,
    namespace_class,
    namespace_module,
    new_path_param,
    resource_class,
    resource_module,
    runtime_dots,
    singleton_attr,
    singleton_class,
    singleton_module,
)
from okapipy.generator.emit.op_context import (
    Shape,
    collect_model_names,
    filter_model_name,
    iterator_item_type,
    op_context,
)
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
    for ns in api.namespaces:
        out.update(
            _emit_namespace(
                env, ns, project_context, package_path, available, shape, mount_relpath
            )
        )
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
        "class_docstring": build_namespace_class_docstring(
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
        "class_docstring": build_namespace_class_docstring(
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
        id_param = new_path_param(coll.path, coll.resource.path)
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
        filter_model_name(fetch_op.item_model, available_models)
        if fetch_op is not None
        else None
    )
    # Collection imports only what its emitted code references: the create
    # request/response models and (when present) the iterator's item type.
    import_names = collect_model_names([create_op], available_models)
    if fetch_item_model is not None:
        import_names.add(fetch_item_model)
    model_imports = sorted(import_names)
    create_op_ctx = op_context(create_op, available_models, shape)
    class_doc = build_collection_class_docstring(
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
            filter_model_name(fetch_op.response_model, available_models)
            if fetch_op is not None
            else None
        ),
        "fetch_item_model": fetch_item_model,
        "item_type": iterator_item_type(fetch_item_model, shape),
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
        collect_model_names(
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
        "retrieve_op": op_context(resource.retrieve, available_models, shape),
        "update_op": op_context(resource.update, available_models, shape),
        "patch_op": op_context(resource.partial_update, available_models, shape),
        "delete_op": op_context(resource.delete, available_models, shape),
        "child_collections": child_collections,
        "child_singletons": child_singletons,
        "actions": actions,
        "model_imports": model_imports,
        "class_docstring": build_resource_class_docstring(
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
        collect_model_names(
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
        "retrieve_op": op_context(singleton.retrieve, available_models, shape),
        "update_op": op_context(singleton.update, available_models, shape),
        "patch_op": op_context(singleton.partial_update, available_models, shape),
        "delete_op": op_context(singleton.delete, available_models, shape),
        "child_collections": child_collections,
        "child_singletons": child_singletons,
        "actions": actions,
        "model_imports": model_imports,
        "class_docstring": build_singleton_class_docstring(
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


def _emit_action(
    env: Environment,
    action: Action,
    project_context: Mapping[str, Any],
    package_path: str,
    available_models: set[str] | None,
    shape: Shape,
    mount_relpath: str = "",
) -> dict[str, str]:
    operations: list[dict[str, Any]] = [
        op
        for op in (op_context(o, available_models, shape) for o in action.operations)
        if op is not None
    ]
    single_op = operations[0] if len(operations) == 1 else None
    model_imports = sorted(collect_model_names(action.operations, available_models))
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
    any_has_body = any(op["has_body"] for op in operations)
    ctx = {
        **project_context,
        "class_name": action_class(action),
        "path_template": action.path,
        "operations": operations,
        "single_op": single_op,
        "any_has_body": any_has_body,
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
