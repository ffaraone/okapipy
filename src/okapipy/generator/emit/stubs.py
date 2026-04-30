"""Emit user-layer subclass stubs that customers customize.

Stubs are `class X(XBase): pass` files placed at the user-layer paths (mirror
of `base/` minus the suffix). They are written exactly once on first
generation (`one_shot=True`) and are never touched again by subsequent runs.

Layout produced (sibling of the regenerated `base/` tree):

    src/{package_path}/
    ├── __init__.py            # empty (decision: customization.md §11.2)
    ├── client.py              # class <Client>(<Client>Base): pass; async sibling
    ├── namespaces/<ns>.py     # class <Ns>Namespace(<Ns>NamespaceBase): pass; ...
    ├── collections/<c>.py     # class <C>Collection(<C>CollectionBase): pass; ...
    ├── resources/<r>.py       # class <R>Resource(<R>ResourceBase): pass; ...
    └── actions/<a>.py         # class <A>Action(<A>ActionBase): pass; ...

User-layer module names match the base layer (collisions are by design — the
import path's `base/` segment is what disambiguates).
"""

from __future__ import annotations

from okapipy.generator.emit.walk import (
    _action_class,
    _action_module,
    _collection_class,
    _collection_module,
    _namespace_class,
    _namespace_module,
    _resource_class,
    _resource_module,
)
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
    "Add custom methods, override `__<child>_factory__` hooks to point at your\n"
    "own subclasses, or replace generated behavior entirely. Regenerating the\n"
    "client leaves this file untouched.\n"
    '"""\n'
)


def emit_stubs(
    api: APIModel,
    package: str,
    package_path: str,
    client_class: str,
) -> dict[str, GeneratedFile]:
    """Return one-shot user-layer stubs as a virtual-FS dict.

    Args:
        api: parsed `APIModel` produced by the parser.
        package: dotted package path (e.g. `acme.commerce`).
        package_path: slash-form of the same (`acme/commerce`).
        client_class: PascalCase client class name (e.g. `AcmeClient`).
    """
    out: dict[str, GeneratedFile] = {}
    out[f"src/{package_path}/__init__.py"] = _stub("")
    out[f"src/{package_path}/client.py"] = _stub_pair(
        from_module=f"{package}.base.client",
        base_class=f"{client_class}Base",
        user_class=client_class,
    )
    for ns in api.namespaces:
        _walk_namespace(ns, out, package, package_path)
    for coll in api.collections:
        _walk_collection(coll, out, package, package_path)
    # Empty __init__.py for each subdir we populated.
    for subdir in ("namespaces", "collections", "resources", "actions"):
        if any(p.startswith(f"src/{package_path}/{subdir}/") for p in out):
            out.setdefault(f"src/{package_path}/{subdir}/__init__.py", _stub(""))
    return out


def _stub(content: str) -> GeneratedFile:
    """Return a `GeneratedFile` marked one-shot."""
    return GeneratedFile(content=content, one_shot=True)


def _stub_pair(
    *, from_module: str, base_class: str, user_class: str
) -> GeneratedFile:
    """Render a `class X(XBase): pass` stub plus its `Async` sibling.

    Import names are emitted alphabetically — isort treats `Async{X}Base` as
    sorting before `{X}Base` so the generated stub passes ruff's I001 check
    without a follow-up `--fix` pass.
    """
    names = sorted([base_class, f"Async{base_class}"])
    body = (
        f"{STUB_DOCSTRING}\n"
        "from __future__ import annotations\n"
        "\n"
        f"from {from_module} import (\n"
        f"    {names[0]},\n"
        f"    {names[1]},\n"
        ")\n"
        "\n"
        "\n"
        f"class {user_class}({user_class}Base):\n"
        "    pass\n"
        "\n"
        "\n"
        f"class Async{user_class}(Async{user_class}Base):\n"
        "    pass\n"
    )
    return _stub(body)


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
    """Emit an action stub. Actions have no children."""
    base_class = _action_class(action)
    user_class = base_class.removesuffix("Base")
    module = _action_module(action)
    out[f"src/{package_path}/actions/{module}.py"] = _stub_pair(
        from_module=f"{package}.base.actions.{module}",
        base_class=base_class,
        user_class=user_class,
    )
