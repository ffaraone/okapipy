"""Emit user-layer subclass stubs that customers customize.

Stubs are subclass files placed at the user-layer paths (mirror of `base/`
minus the `Base` suffix on class names). They are written exactly once on
first generation (`one_shot=True`) and are never touched again by subsequent
runs.

On first emission each stub **auto-wires** every `__<child>_factory__` hook
to point at the user-layer subclass tree, so out-of-the-box `client.commerce.orders`
returns the user's `OrdersCollection` rather than the `Base` default. Spec
growth (a new collection added later) is the one case the auto-wiring can't
cover, since parent stubs are one-shot — Phase 4's drift detection emits a
warning telling the user the exact line to add.

Layout produced (sibling of the regenerated `base/` tree):

    src/{package_path}/
    ├── __init__.py            # empty (decision: customization.md §11.2)
    ├── client.py              # class Client(ClientBase): __ns_factory__ = ...
    ├── namespaces/<ns>.py     # class CommerceNamespace(...): __orders_factory__ = ...
    ├── collections/<c>.py     # class OrdersCollection(...): __resource_factory__ = ...
    ├── resources/<r>.py       # class OrderResource(...): __lines_factory__ = ...
    └── actions/<a>.py         # class OrderSubmitAction(...): pass  (no children)

User-layer module names match the base layer (collisions are by design — the
import path's `base/` segment is what disambiguates).
"""

from __future__ import annotations

from dataclasses import dataclass

from okapipy.generator.emit.walk import (
    _action_attr,
    _action_class,
    _action_module,
    _collection_attr,
    _collection_class,
    _collection_module,
    _factory_attr,
    _namespace_class,
    _namespace_module,
    _resource_class,
    _resource_module,
)
from okapipy.generator.templating import _snake_case
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Resource,
)

STUB_DOCSTRING = (
    '"""User-layer subclass — okapipy emits this once and never overwrites it.\n'
    "\n"
    "Add custom methods, repoint `__<child>_factory__` hooks at your own\n"
    "subclasses, or replace generated behavior entirely. Regenerating the\n"
    "client leaves this file untouched.\n"
    '"""\n'
)


@dataclass(frozen=True)
class _ChildWiring:
    """One `__<attr>_factory__ = UserClass` line plus the import that supports it."""

    factory_attr: str       # e.g. "__orders_factory__"
    user_class: str         # sync user class, e.g. "OrdersCollection"; async is "Async..."
    user_module_path: str   # dotted path to user-layer module, e.g. "p.collections.orders"


def emit_stubs(
    api: APIModel,
    package: str,
    package_path: str,
    client_class: str,
) -> dict[str, GeneratedFile]:
    """Return one-shot user-layer stubs as a virtual-FS dict."""
    out: dict[str, GeneratedFile] = {}
    out[f"src/{package_path}/__init__.py"] = _stub("")
    out[f"src/{package_path}/client.py"] = _stub_pair(
        from_module=f"{package}.base.client",
        base_class=f"{client_class}Base",
        user_class=client_class,
        wirings=_client_wirings(api, package),
    )
    for ns in api.namespaces:
        _walk_namespace(ns, out, package, package_path)
    for coll in api.collections:
        _walk_collection(coll, out, package, package_path)
    for subdir in ("namespaces", "collections", "resources", "actions"):
        if any(p.startswith(f"src/{package_path}/{subdir}/") for p in out):
            out.setdefault(f"src/{package_path}/{subdir}/__init__.py", _stub(""))
    return out


def _stub(content: str) -> GeneratedFile:
    """Return a `GeneratedFile` marked one-shot."""
    return GeneratedFile(content=content, one_shot=True)


def _stub_pair(
    *,
    from_module: str,
    base_class: str,
    user_class: str,
    wirings: list[_ChildWiring] | None = None,
) -> GeneratedFile:
    """Render a `class X(XBase)` stub plus its async sibling, with factory wiring.

    `wirings=None` (or an empty list) produces the `pass`-bodied stub used for
    leaf nodes (actions). Any non-empty wiring list emits one
    `__<attr>_factory__ = UserClass` line per child on the sync class and the
    `Async`-prefixed counterpart on the async class.
    """
    wirings = list(wirings or [])
    base_names = sorted([base_class, f"Async{base_class}"])
    # Build (module, names) tuples then sort by module path so the rendered
    # import block is isort-clean (ruff I001) without a follow-up `--fix` pass.
    blocks: list[tuple[str, list[str]]] = [(from_module, base_names)]
    for w in wirings:
        blocks.append(
            (w.user_module_path, sorted([w.user_class, f"Async{w.user_class}"]))
        )
    blocks.sort(key=lambda b: b[0])
    import_lines = [_import_block(module, names) for module, names in blocks]
    sync_body = _factory_lines(wirings, async_=False)
    async_body = _factory_lines(wirings, async_=True)
    body = (
        f"{STUB_DOCSTRING}\n"
        "from __future__ import annotations\n"
        "\n"
        + "\n".join(import_lines)
        + "\n\n\n"
        + f"class {user_class}({user_class}Base):\n{sync_body}\n\n\n"
        + f"class Async{user_class}(Async{user_class}Base):\n{async_body}\n"
    )
    return _stub(body)


def _import_block(module_path: str, names: list[str]) -> str:
    """Render a parenthesized multi-line `from X import (A, B,)` import line."""
    name_lines = "".join(f"    {n},\n" for n in names)
    return f"from {module_path} import (\n{name_lines})"


def _factory_lines(wirings: list[_ChildWiring], *, async_: bool) -> str:
    """Render the body of a stub class — factory assignments or `pass`."""
    if not wirings:
        return "    pass"
    prefix = "Async" if async_ else ""
    return "\n".join(
        f"    {w.factory_attr} = {prefix}{w.user_class}" for w in wirings
    )


# --------------------------------------------------------------------------- #
# Wiring builders — enumerate each parent's children to feed `_stub_pair`.    #
# --------------------------------------------------------------------------- #


def _client_wirings(api: APIModel, package: str) -> list[_ChildWiring]:
    """Children of the top-level client: top-level namespaces + collections."""
    out: list[_ChildWiring] = []
    for ns in api.namespaces:
        out.append(_namespace_child_wiring(ns, package))
    for coll in api.collections:
        out.append(_collection_child_wiring(coll, package))
    return out


def _namespace_wirings(ns: Namespace, package: str) -> list[_ChildWiring]:
    """Children of a namespace: sub-namespaces + collections."""
    out: list[_ChildWiring] = []
    for child in ns.namespaces:
        out.append(_namespace_child_wiring(child, package))
    for coll in ns.collections:
        out.append(_collection_child_wiring(coll, package))
    return out


def _collection_wirings(coll: Collection, package: str) -> list[_ChildWiring]:
    """Children of a collection: at most one resource + zero-or-more actions."""
    out: list[_ChildWiring] = []
    if coll.resource is not None:
        out.append(_resource_child_wiring(coll.resource, package))
    for action in coll.actions:
        out.append(_action_child_wiring(action, package))
    return out


def _resource_wirings(resource: Resource, package: str) -> list[_ChildWiring]:
    """Children of a resource: sub-collections + actions."""
    out: list[_ChildWiring] = []
    for coll in resource.collections:
        out.append(_collection_child_wiring(coll, package))
    for action in resource.actions:
        out.append(_action_child_wiring(action, package))
    return out


def _namespace_child_wiring(ns: Namespace, package: str) -> _ChildWiring:
    base = _namespace_class(ns)
    return _ChildWiring(
        factory_attr=_factory_attr(_snake_case(ns.name)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.namespaces.{_namespace_module(ns)}",
    )


def _collection_child_wiring(coll: Collection, package: str) -> _ChildWiring:
    base = _collection_class(coll)
    return _ChildWiring(
        factory_attr=_factory_attr(_collection_attr(coll)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.collections.{_collection_module(coll)}",
    )


def _resource_child_wiring(resource: Resource, package: str) -> _ChildWiring:
    base = _resource_class(resource)
    return _ChildWiring(
        factory_attr=_factory_attr("resource"),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.resources.{_resource_module(resource)}",
    )


def _action_child_wiring(action: Action, package: str) -> _ChildWiring:
    base = _action_class(action)
    return _ChildWiring(
        factory_attr=_factory_attr(_action_attr(action)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.actions.{_action_module(action)}",
    )


# --------------------------------------------------------------------------- #
# Walkers                                                                     #
# --------------------------------------------------------------------------- #


def _walk_namespace(
    ns: Namespace,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit a namespace stub and recurse into children."""
    base_class = _namespace_class(ns)
    user_class = base_class.removesuffix("Base")
    module = _namespace_module(ns)
    out[f"src/{package_path}/namespaces/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.namespaces.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=_namespace_wirings(ns, package),
    )
    for child in ns.namespaces:
        _walk_namespace(child, out, package, package_path)
    for coll in ns.collections:
        _walk_collection(coll, out, package, package_path)


def _walk_collection(
    coll: Collection,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit a collection stub and recurse into the resource and any actions."""
    base_class = _collection_class(coll)
    user_class = base_class.removesuffix("Base")
    module = _collection_module(coll)
    out[f"src/{package_path}/collections/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.collections.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=_collection_wirings(coll, package),
    )
    if coll.resource is not None:
        _walk_resource(coll.resource, out, package, package_path)
    for action in coll.actions:
        _walk_action(action, out, package, package_path)


def _walk_resource(
    resource: Resource,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit a resource stub and recurse into sub-collections and actions."""
    base_class = _resource_class(resource)
    user_class = base_class.removesuffix("Base")
    module = _resource_module(resource)
    out[f"src/{package_path}/resources/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.resources.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=_resource_wirings(resource, package),
    )
    for coll in resource.collections:
        _walk_collection(coll, out, package, package_path)
    for action in resource.actions:
        _walk_action(action, out, package, package_path)


def _walk_action(
    action: Action,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit an action stub. Actions are leaves — no factory wiring."""
    base_class = _action_class(action)
    user_class = base_class.removesuffix("Base")
    module = _action_module(action)
    out[f"src/{package_path}/actions/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.actions.{module}",
        base_class=base_class,
        user_class=user_class,
    )
