"""Emit pytest-based tests for every generated node.

Walks the same `APIModel` the package emitter walks and produces one test
module per node, plus a shared `conftest.py` with sync/async client fixtures.
Generated tests use `pytest`, `pytest-asyncio`, `pytest-httpx`, and
`pytest-mock`; HTTP traffic is mocked via the `httpx_mock` fixture.

Each test module is rendered as a Python source file and run through
`render_python` (isort + ruff format) so the output matches the rest of the
generated tree's lint rules. The full subtree is emitted as one-shot — tests
are scaffolding, customer-owned after first generation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from jinja2 import Environment

from okapipy.generator.emit.names import (
    action_attr,
    action_module,
    collection_attr,
    collection_module,
    namespace_module,
    resource_module,
    singleton_attr,
    singleton_module,
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

SAMPLE_ID = "sample-id"


def emit_tests(
    env: Environment,
    api: APIModel,
    project_context: Mapping[str, Any],
    top_package: str,
    *,
    mount_relpath: str = "",
    emit_root: bool = True,
) -> dict[str, str]:
    """Render `conftest.py` plus one test module per node in `api`.

    `top_package` is the first dotted segment of the generated package and is
    forwarded to ruff's isort as `known-first-party` — without it, `from
    <pkg> import ...` lands in the third-party group here but in the
    first-party group inside the generated project, producing an `I001` lint
    error when users run `ruff check`.

    Every emitted path lives under `tests/<package_path>/...`, mirroring the
    `src/<package_path>/...` layout. This keeps two flavors of the client
    (e.g. `acme.commerce.models` and `acme.commerce.dicts`) from clobbering
    each other's test scaffolding when they share a project directory and
    avoids pytest's default-import-mode filename collisions on `test_*.py`.

    Empty `__init__.py` markers are emitted at every directory along the
    package path (and at `tests/` itself) so pytest's standard `prepend`
    importer treats each test module as a unique fully-qualified Python
    module.

    Returns a virtual-FS dict keyed on POSIX paths under `tests/`. The caller
    decides the lifecycle (the generator marks them all one-shot so customer
    edits survive regeneration).
    """
    package: str = project_context["package"]
    package_path = package.replace(".", "/")
    tests_root = f"tests/{package_path}"
    mount_tests_root = f"{tests_root}/{mount_relpath}".rstrip("/")
    initial_chain = mount_relpath.rstrip("/").replace("/", ".")
    out: dict[str, str] = {}
    if emit_root:
        out[f"{tests_root}/conftest.py"] = render_python(
            env,
            "tests/conftest.py.jinja",
            project_context,
            known_first_party=top_package,
        )
        out[f"{tests_root}/test_client.py"] = render_python(
            env,
            "tests/test_client.py.jinja",
            project_context,
            known_first_party=top_package,
        )
    for ns in api.namespaces:
        _emit_namespace_tests(
            env,
            ns,
            project_context,
            out,
            parent_chain=initial_chain,
            top_package=top_package,
            tests_root=mount_tests_root,
        )
    for coll in api.collections:
        _emit_collection_tests(
            env,
            coll,
            project_context,
            out,
            parent_chain=initial_chain,
            top_package=top_package,
            tests_root=mount_tests_root,
        )
    for sing in api.singletons:
        _emit_singleton_tests(
            env,
            sing,
            project_context,
            out,
            parent_chain=initial_chain,
            top_package=top_package,
            tests_root=mount_tests_root,
        )
    for action in api.actions:
        _emit_action_tests(
            env,
            action,
            project_context,
            out,
            parent_chain=initial_chain,
            top_package=top_package,
            tests_root=mount_tests_root,
        )
    # Empty `__init__.py` at every directory that contains a test file (or any
    # ancestor up to `tests/`). pytest's default `prepend` importer otherwise
    # falls back to the test module's basename, which collides across flavors
    # (`tests/<pkg>.models/.../test_orders.py` vs `tests/<pkg>.dicts/.../test_orders.py`)
    # — the markers turn each file into a unique fully-qualified module.
    for marker_path in _init_marker_paths(out.keys()):
        out.setdefault(marker_path, "")
    return out


def _init_marker_paths(file_paths: Iterable[str]) -> set[str]:
    """Return `tests/.../__init__.py` paths for every directory containing a file.

    Walks up from each emitted file to `tests/`, collecting every intermediate
    directory. The result also includes `tests/__init__.py` itself so `tests`
    is a regular Python package (required for pytest's basename collision
    guard to work without flipping `import_mode`).
    """
    markers: set[str] = set()
    for path in file_paths:
        if not path.startswith("tests/"):
            continue
        parts = path.split("/")
        # Drop the filename, keep every directory along the way (excluding the
        # filesystem root above `tests/`).
        for end in range(1, len(parts)):
            directory = "/".join(parts[:end])
            markers.add(f"{directory}/__init__.py")
    return markers


def _emit_namespace_tests(
    env: Environment,
    ns: Namespace,
    project_context: Mapping[str, Any],
    out: dict[str, str],
    parent_chain: str,
    top_package: str,
    tests_root: str,
) -> None:
    attr = snake_case(ns.name)
    chain = _join_chain(parent_chain, attr)
    test_attr = _safe_test_attr(chain)
    ctx = {
        **project_context,
        "accessor_chain": chain,
        "test_attr": test_attr,
    }
    out[f"{tests_root}/namespaces/test_{namespace_module(ns)}.py"] = render_python(
        env, "tests/test_namespace.py.jinja", ctx, known_first_party=top_package
    )
    for child in ns.namespaces:
        _emit_namespace_tests(
            env,
            child,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for coll in ns.collections:
        _emit_collection_tests(
            env,
            coll,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for sing in ns.singletons:
        _emit_singleton_tests(
            env,
            sing,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for action in ns.actions:
        _emit_action_tests(
            env,
            action,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )


def _emit_collection_tests(
    env: Environment,
    coll: Collection,
    project_context: Mapping[str, Any],
    out: dict[str, str],
    parent_chain: str,
    top_package: str,
    tests_root: str,
) -> None:
    attr = collection_attr(coll)
    chain = _join_chain(parent_chain, attr)
    test_attr = _safe_test_attr(chain)
    create_method = coll.create.method if coll.create is not None else "POST"
    ctx = {
        **project_context,
        "accessor_chain": chain,
        "test_attr": test_attr,
        "has_create": coll.create is not None,
        "create_method": create_method,
    }
    out[f"{tests_root}/collections/test_{collection_module(coll)}.py"] = render_python(
        env, "tests/test_collection.py.jinja", ctx, known_first_party=top_package
    )
    if coll.resource is not None:
        # Sub-resource access requires subscript with a sample id.
        resource_chain = f'{chain}["{SAMPLE_ID}"]'
        _emit_resource_tests(
            env,
            coll.resource,
            project_context,
            out,
            parent_chain=resource_chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for action in coll.actions:
        _emit_action_tests(
            env,
            action,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )


def _emit_resource_tests(
    env: Environment,
    resource: Resource,
    project_context: Mapping[str, Any],
    out: dict[str, str],
    parent_chain: str,
    top_package: str,
    tests_root: str,
) -> None:
    chain = parent_chain  # resource attaches via subscript already in chain
    test_attr = _safe_test_attr(f"{chain}_{snake_case(resource.name)}")
    ctx = {
        **project_context,
        "accessor_chain": chain,
        "test_attr": test_attr,
        **_op_flags(
            retrieve=resource.retrieve,
            update=resource.update,
            patch=resource.partial_update,
            delete=resource.delete,
        ),
    }
    out[f"{tests_root}/resources/test_{resource_module(resource)}.py"] = render_python(
        env, "tests/test_resource.py.jinja", ctx, known_first_party=top_package
    )
    for child_coll in resource.collections:
        _emit_collection_tests(
            env,
            child_coll,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for child_sing in resource.singletons:
        _emit_singleton_tests(
            env,
            child_sing,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for action in resource.actions:
        _emit_action_tests(
            env,
            action,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )


def _emit_singleton_tests(
    env: Environment,
    singleton: Singleton,
    project_context: Mapping[str, Any],
    out: dict[str, str],
    parent_chain: str,
    top_package: str,
    tests_root: str,
) -> None:
    attr = singleton_attr(singleton)
    chain = _join_chain(parent_chain, attr)
    test_attr = _safe_test_attr(chain)
    ctx = {
        **project_context,
        "accessor_chain": chain,
        "test_attr": test_attr,
        **_op_flags(
            retrieve=singleton.retrieve,
            update=singleton.update,
            patch=singleton.partial_update,
            delete=singleton.delete,
        ),
    }
    out[f"{tests_root}/singletons/test_{singleton_module(singleton)}.py"] = (
        render_python(
            env, "tests/test_singleton.py.jinja", ctx, known_first_party=top_package
        )
    )
    for child_coll in singleton.collections:
        _emit_collection_tests(
            env,
            child_coll,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for sub in singleton.singletons:
        _emit_singleton_tests(
            env,
            sub,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )
    for action in singleton.actions:
        _emit_action_tests(
            env,
            action,
            project_context,
            out,
            parent_chain=chain,
            top_package=top_package,
            tests_root=tests_root,
        )


def _emit_action_tests(
    env: Environment,
    action: Action,
    project_context: Mapping[str, Any],
    out: dict[str, str],
    parent_chain: str,
    top_package: str,
    tests_root: str,
) -> None:
    if not action.operations:
        return
    attr = action_attr(action)
    chain = _join_chain(parent_chain, attr)
    test_attr = _safe_test_attr(chain)
    operations = [
        {
            "method": op.method,
            "has_body": bool(op.request_model) or bool(op.request_model_members),
        }
        for op in action.operations
    ]
    single_op = operations[0] if len(operations) == 1 else None
    ctx = {
        **project_context,
        "accessor_chain": chain,
        "test_attr": test_attr,
        "operations": operations,
        "single_op": single_op,
    }
    out[f"{tests_root}/actions/test_{action_module(action)}.py"] = render_python(
        env, "tests/test_action.py.jinja", ctx, known_first_party=top_package
    )


def _op_flags(
    *,
    retrieve: Operation | None,
    update: Operation | None,
    patch: Operation | None,
    delete: Operation | None,
) -> dict[str, Any]:
    """Return the `has_*` / `*_method` template fields for resource/singleton ops."""
    return {
        "has_retrieve": retrieve is not None,
        "has_update": update is not None,
        "has_patch": patch is not None,
        "has_delete": delete is not None,
        "retrieve_method": retrieve.method if retrieve is not None else "GET",
        "update_method": update.method if update is not None else "PUT",
        "patch_method": patch.method if patch is not None else "PATCH",
        "delete_method": delete.method if delete is not None else "DELETE",
    }


def _join_chain(parent: str, attr: str) -> str:
    """Append `attr` to a dotted accessor chain, handling subscript boundaries.

    Subscripts (`["sample-id"]`) sit at the end of `parent` directly. Property
    attrs join with a leading dot. Top-level (empty `parent`) starts the chain.
    """
    if not parent:
        return attr
    return f"{parent}.{attr}"


def _safe_test_attr(chain: str) -> str:
    """Convert an access chain into a Python identifier suitable for test names.

    Replaces dots, brackets, quotes, and hyphens with underscores so the result
    is a valid identifier. Collapses runs of underscores produced by subscripts
    (e.g. `orders["sample-id"]` → `orders_sample_id`).
    """
    raw = chain.replace(".", "_").replace("[", "_").replace("]", "_")
    raw = raw.replace('"', "").replace("'", "").replace("-", "_")
    while "__" in raw:
        raw = raw.replace("__", "_")
    return raw.strip("_")
