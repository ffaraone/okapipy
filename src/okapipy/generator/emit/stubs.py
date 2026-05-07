"""Emit user-layer subclass stubs that customers customize.

Stubs are subclass files placed at the user-layer paths (mirror of `base/`
minus the `Base` suffix on class names). They are written exactly once on
first generation (`one_shot=True`) and are never touched again by subsequent
runs.

On first emission each stub **auto-wires** every `__<child>_factory__` hook
to point at the user-layer subclass tree, so out-of-the-box
`client.commerce.orders` returns the user's `OrdersCollection` rather than the
`Base` default. Spec growth — a new collection added later — is the one case
auto-wiring cannot cover, because parent stubs are never re-emitted; the
manifest-based drift detection in `vfs.py` emits a warning that names the
exact line the customer needs to add by hand.

Layout produced (sibling of the regenerated `base/` tree):

    src/{package_path}/
    ├── __init__.py            # left empty so the customer can choose what to re-export
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
    action_attr,
    action_class,
    action_module,
    collection_attr,
    collection_class,
    collection_module,
    factory_attr,
    namespace_class,
    namespace_module,
    resource_class,
    resource_module,
    singleton_attr,
    singleton_class,
    singleton_module,
)
from okapipy.generator.templating import snake_case
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Resource,
    Singleton,
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
class ChildWiring:
    """One `__<attr>_factory__ = UserClass` line plus the import that supports it."""

    factory_attr: str  # e.g. "__orders_factory__"
    user_class: str  # sync user class, e.g. "OrdersCollection"; async is "Async..."
    user_module_path: (
        str  # dotted path to user-layer module, e.g. "p.collections.orders"
    )


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
        wirings=client_wirings(api, package),
    )
    for ns in api.namespaces:
        _walk_namespace(ns, out, package, package_path)
    for coll in api.collections:
        _walk_collection(coll, out, package, package_path)
    for sing in api.singletons:
        _walk_singleton(sing, out, package, package_path)
    for action in api.actions:
        _walk_action(action, out, package, package_path)
    for subdir in ("namespaces", "collections", "resources", "singletons", "actions"):
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
    wirings: list[ChildWiring] | None = None,
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


def _factory_lines(wirings: list[ChildWiring], *, async_: bool) -> str:
    """Render the body of a stub class — factory assignments or `pass`."""
    if not wirings:
        return "    pass"
    prefix = "Async" if async_ else ""
    return "\n".join(f"    {w.factory_attr} = {prefix}{w.user_class}" for w in wirings)


# --------------------------------------------------------------------------- #
# Wiring builders — enumerate each parent's children to feed `_stub_pair`.    #
# --------------------------------------------------------------------------- #


def client_wirings(api: APIModel, package: str) -> list[ChildWiring]:
    """Children of the top-level client: namespaces, collections, singletons, actions."""
    out: list[ChildWiring] = []
    for ns in api.namespaces:
        out.append(_namespace_child_wiring(ns, package))
    for coll in api.collections:
        out.append(_collection_child_wiring(coll, package))
    for sing in api.singletons:
        out.append(_singleton_child_wiring(sing, package))
    for action in api.actions:
        out.append(_action_child_wiring(action, package))
    return out


def namespace_wirings(ns: Namespace, package: str) -> list[ChildWiring]:
    """Children of a namespace: sub-namespaces, collections, singletons, actions."""
    out: list[ChildWiring] = []
    for child in ns.namespaces:
        out.append(_namespace_child_wiring(child, package))
    for coll in ns.collections:
        out.append(_collection_child_wiring(coll, package))
    for sing in ns.singletons:
        out.append(_singleton_child_wiring(sing, package))
    for action in ns.actions:
        out.append(_action_child_wiring(action, package))
    return out


def collection_wirings(coll: Collection, package: str) -> list[ChildWiring]:
    """Children of a collection: at most one resource + zero-or-more actions."""
    out: list[ChildWiring] = []
    if coll.resource is not None:
        out.append(_resource_child_wiring(coll.resource, package))
    for action in coll.actions:
        out.append(_action_child_wiring(action, package))
    return out


def resource_wirings(resource: Resource, package: str) -> list[ChildWiring]:
    """Children of a resource: sub-collections, sub-singletons, actions."""
    out: list[ChildWiring] = []
    for coll in resource.collections:
        out.append(_collection_child_wiring(coll, package))
    for sing in resource.singletons:
        out.append(_singleton_child_wiring(sing, package))
    for action in resource.actions:
        out.append(_action_child_wiring(action, package))
    return out


def singleton_wirings(singleton: Singleton, package: str) -> list[ChildWiring]:
    """Children of a singleton: sub-collections, sub-singletons, actions."""
    out: list[ChildWiring] = []
    for coll in singleton.collections:
        out.append(_collection_child_wiring(coll, package))
    for sub in singleton.singletons:
        out.append(_singleton_child_wiring(sub, package))
    for action in singleton.actions:
        out.append(_action_child_wiring(action, package))
    return out


def _namespace_child_wiring(ns: Namespace, package: str) -> ChildWiring:
    base = namespace_class(ns)
    return ChildWiring(
        factory_attr=factory_attr(snake_case(ns.name)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.namespaces.{namespace_module(ns)}",
    )


def _collection_child_wiring(coll: Collection, package: str) -> ChildWiring:
    base = collection_class(coll)
    return ChildWiring(
        factory_attr=factory_attr(collection_attr(coll)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.collections.{collection_module(coll)}",
    )


def _resource_child_wiring(resource: Resource, package: str) -> ChildWiring:
    base = resource_class(resource)
    return ChildWiring(
        factory_attr=factory_attr("resource"),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.resources.{resource_module(resource)}",
    )


def _action_child_wiring(action: Action, package: str) -> ChildWiring:
    base = action_class(action)
    return ChildWiring(
        factory_attr=factory_attr(action_attr(action)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.actions.{action_module(action)}",
    )


def _singleton_child_wiring(singleton: Singleton, package: str) -> ChildWiring:
    base = singleton_class(singleton)
    return ChildWiring(
        factory_attr=factory_attr(singleton_attr(singleton)),
        user_class=base.removesuffix("Base"),
        user_module_path=f"{package}.singletons.{singleton_module(singleton)}",
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
    base_class = namespace_class(ns)
    user_class = base_class.removesuffix("Base")
    module = namespace_module(ns)
    out[f"src/{package_path}/namespaces/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.namespaces.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=namespace_wirings(ns, package),
    )
    for child in ns.namespaces:
        _walk_namespace(child, out, package, package_path)
    for coll in ns.collections:
        _walk_collection(coll, out, package, package_path)
    for sing in ns.singletons:
        _walk_singleton(sing, out, package, package_path)
    for action in ns.actions:
        _walk_action(action, out, package, package_path)


def _walk_collection(
    coll: Collection,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit a collection stub and recurse into the resource and any actions."""
    base_class = collection_class(coll)
    user_class = base_class.removesuffix("Base")
    module = collection_module(coll)
    out[f"src/{package_path}/collections/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.collections.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=collection_wirings(coll, package),
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
    """Emit a resource stub and recurse into sub-collections, sub-singletons, and actions."""
    base_class = resource_class(resource)
    user_class = base_class.removesuffix("Base")
    module = resource_module(resource)
    out[f"src/{package_path}/resources/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.resources.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=resource_wirings(resource, package),
    )
    for coll in resource.collections:
        _walk_collection(coll, out, package, package_path)
    for sing in resource.singletons:
        _walk_singleton(sing, out, package, package_path)
    for action in resource.actions:
        _walk_action(action, out, package, package_path)


def _walk_singleton(
    singleton: Singleton,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit a singleton stub and recurse into children."""
    base_class = singleton_class(singleton)
    user_class = base_class.removesuffix("Base")
    module = singleton_module(singleton)
    out[f"src/{package_path}/singletons/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.singletons.{module}",
        base_class=base_class,
        user_class=user_class,
        wirings=singleton_wirings(singleton, package),
    )
    for coll in singleton.collections:
        _walk_collection(coll, out, package, package_path)
    for sub in singleton.singletons:
        _walk_singleton(sub, out, package, package_path)
    for action in singleton.actions:
        _walk_action(action, out, package, package_path)


def _walk_action(
    action: Action,
    out: dict[str, GeneratedFile],
    package: str,
    package_path: str,
) -> None:
    """Emit an action stub. Actions are leaves — no factory wiring."""
    base_class = action_class(action)
    user_class = base_class.removesuffix("Base")
    module = action_module(action)
    out[f"src/{package_path}/actions/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.actions.{module}",
        base_class=base_class,
        user_class=user_class,
    )
