"""IDE-friendly docstrings on generated client classes and accessors.

Two layers of coverage live here:

* **Helper unit tests** call the public docstring builders in
  `okapipy.generator.emit.walk` against hand-built parser nodes — small,
  fast, and pinned to substring assertions so wording polish doesn't ripple
  through fixtures.
* **Golden-file checks** generate a tree from the `nested.yaml` fixture
  and assert the rendered Python carries the expected sections in the
  expected places.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from okapipy.generator import generate
from okapipy.generator.emit.walk import (
    ChildRef,
    action_accessor_docstring,
    action_meta_inline,
    action_one_line,
    build_action_docstring,
    build_client_class_docstring,
    build_docstring,
    collection_one_line,
    getitem_accessor_docstring,
    namespace_accessor_docstring,
    node_one_line,
    singleton_accessor_docstring,
    singleton_one_line,
)
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse
from okapipy.parser.model import (
    Action,
    Collection,
    Namespace,
    Operation,
    Singleton,
)


def test_node_one_line_prefers_summary_over_description() -> None:
    """`node_one_line` returns the summary when both summary and description are set."""
    out = node_one_line("Pick me.", "Other text.", fallback="fallback")

    assert out == "Pick me."


def test_node_one_line_falls_back_to_description() -> None:
    """`node_one_line` returns the description when summary is empty."""
    out = node_one_line(None, "Description text", fallback="fallback")

    assert out == "Description text"


def test_node_one_line_falls_back_to_fallback() -> None:
    """`node_one_line` returns the fallback when summary and description are both empty."""
    out = node_one_line(None, "", fallback="Synth fallback.")

    assert out == "Synth fallback."


def test_node_one_line_trims_to_first_sentence() -> None:
    """`node_one_line` keeps only the first sentence of a multi-sentence summary."""
    out = node_one_line(
        "First sentence. Second sentence we don't want.",
        None,
        fallback="x",
    )

    assert out == "First sentence."


def test_node_one_line_keeps_long_first_line_when_no_terminator_in_window() -> None:
    """`node_one_line` returns the first line as-is when no `.`/`?`/`!` appears early."""
    summary = "this is a clause without any terminator that goes on and on and on"
    out = node_one_line(summary, None, fallback="x")

    assert out == summary


def test_namespace_accessor_docstring_is_a_quoted_block() -> None:
    """`namespace_accessor_docstring` always returns a triple-quoted block at indent 8."""
    ns = Namespace(name="admin", description="Admin stuff.")

    rendered = namespace_accessor_docstring(ns)

    assert rendered.lstrip().startswith('"""')
    assert "Admin stuff." in rendered


def test_namespace_accessor_docstring_uses_synth_fallback_when_empty() -> None:
    """`namespace_accessor_docstring` falls back to a structural string when no docs are set."""
    ns = Namespace(name="orphan")

    rendered = namespace_accessor_docstring(ns)

    assert "Namespace `orphan`." in rendered


def test_singleton_accessor_docstring_falls_back_to_retrieve_op() -> None:
    """`singleton_accessor_docstring` uses the retrieve op's summary when the singleton has none."""
    singleton = Singleton(
        name="Me",
        path="/me",
        retrieve=Operation(method="GET", summary="The current user."),
    )

    rendered = singleton_accessor_docstring(singleton)

    assert "The current user." in rendered


def test_action_accessor_docstring_single_op_includes_method_path() -> None:
    """A single-op action's accessor docstring opens with the HTTP method and path."""
    action = Action(
        name="Login",
        path="/login",
        operations=[Operation(method="POST", summary="Exchange creds for a token.")],
    )

    rendered = action_accessor_docstring(action)

    assert "`POST /login`" in rendered
    assert "Exchange creds for a token." in rendered


def test_action_accessor_docstring_multi_op_skips_method_path() -> None:
    """A multi-op action's accessor docstring is a one-liner (no method/path prefix)."""
    action = Action(
        name="Profile",
        path="/profile",
        summary="The current user's profile.",
        operations=[
            Operation(method="GET", summary="Read."),
            Operation(method="PUT", summary="Write."),
        ],
    )

    rendered = action_accessor_docstring(action)

    assert "The current user's profile." in rendered
    assert "GET /profile" not in rendered
    assert "PUT /profile" not in rendered


def test_getitem_accessor_docstring_names_the_id_param() -> None:
    """`getitem_accessor_docstring` mentions the id parameter and the no-HTTP guarantee."""
    rendered = getitem_accessor_docstring(id_param="order_id")

    assert "`order_id`" in rendered
    assert "No HTTP call" in rendered


def test_collection_one_line_falls_back_to_fetch_op() -> None:
    """`collection_one_line` uses the fetch op's summary when the collection has none."""
    coll = Collection(
        name="Orders",
        path="/orders",
        fetch=Operation(method="GET", summary="List all orders"),
    )

    assert collection_one_line(coll) == "List all orders"


def test_singleton_one_line_falls_back_to_retrieve_op() -> None:
    """`singleton_one_line` uses the retrieve op's summary when the singleton has none."""
    singleton = Singleton(
        name="Me",
        path="/me",
        retrieve=Operation(method="GET", summary="The current user."),
    )

    assert singleton_one_line(singleton) == "The current user."


def test_action_one_line_falls_back_to_only_op_for_single_op() -> None:
    """`action_one_line` uses the only op's summary when the action itself has none."""
    action = Action(
        name="Login",
        path="/login",
        operations=[Operation(method="POST", summary="Exchange creds.")],
    )

    assert action_one_line(action) == "Exchange creds."


def test_action_meta_inline_single_op_renders_method_path() -> None:
    """`action_meta_inline` renders a single-op action as `METHOD path` in backticks."""
    action = Action(
        name="Reindex",
        path="/admin/reindex",
        operations=[Operation(method="POST")],
    )

    assert action_meta_inline(action) == "`POST /admin/reindex`"


def test_action_meta_inline_multi_op_renders_path_with_marker() -> None:
    """`action_meta_inline` flags a multi-op action with a `(multiple ops)` marker."""
    action = Action(
        name="Profile",
        path="/profile",
        operations=[Operation(method="GET"), Operation(method="PUT")],
    )

    assert action_meta_inline(action) == "`/profile` (multiple ops)"


def test_build_action_docstring_multi_op_lists_operations_section() -> None:
    """A multi-op action's class docstring opens an `#### Operations` section."""
    action = Action(
        name="Profile",
        path="/profile",
        summary="The user profile endpoint.",
        operations=[
            Operation(method="GET", summary="Read."),
            Operation(method="PUT", summary="Write."),
        ],
    )

    rendered = build_action_docstring(action)

    assert "#### Operations" in rendered
    assert "`GET /profile`" in rendered
    assert "`PUT /profile`" in rendered


def test_build_client_class_docstring_lists_each_populated_section() -> None:
    """`build_client_class_docstring` emits a section per non-empty top-level child kind."""
    top_namespaces = [
        ChildRef(
            attr="admin",
            class_name="AdminNamespaceBase",
            module="admin",
            factory_attr="__admin_factory__",
            one_line="Administrative endpoints.",
        )
    ]
    top_collections = [
        ChildRef(
            attr="orders",
            class_name="OrdersCollectionBase",
            module="orders",
            factory_attr="__orders_factory__",
            one_line="List orders.",
        )
    ]

    rendered = build_client_class_docstring(
        project_name="acme",
        project_version="1.2.3",
        sync=True,
        top_namespaces=top_namespaces,
        top_collections=top_collections,
        top_singletons=[],
        top_actions=[],
    )

    assert "HTTP client for `acme`" in rendered
    assert "(v1.2.3)" in rendered
    assert "#### Top-level namespaces" in rendered
    assert "#### Top-level collections" in rendered
    assert "#### Top-level singletons" not in rendered
    assert "#### Top-level actions" not in rendered
    assert "**`admin`** → `AdminNamespaceBase`" in rendered
    assert "**`orders`** → `OrdersCollectionBase`" in rendered


def test_build_client_class_docstring_async_lead_differs() -> None:
    """The async build flips the lead to mention asynchrony; the map sections match."""
    rendered = build_client_class_docstring(
        project_name="acme",
        project_version=None,
        sync=False,
        top_namespaces=[],
        top_collections=[],
        top_singletons=[],
        top_actions=[],
    )

    assert "Asynchronous HTTP client for `acme`" in rendered


@pytest.fixture
def nested_generated_base(tmp_path: Path, nested_spec_path: Path) -> Path:
    """Generate the nested fixture and return the base directory of the emitted tree."""
    out = tmp_path / "out"
    api = parse(nested_spec_path)
    vfs = generate(
        api,
        raw_spec=nested_spec_path,
        output_dir=out,
        package="docscli",
        client_class="DocsClient",
        project_name="docs-client",
    )
    write_to_disk(vfs, out)
    return out / "src" / "docscli" / "base"


def test_generated_client_class_docstring_includes_namespace_section(
    nested_generated_base: Path,
) -> None:
    """The sync client class lists its top-level namespaces in a `#### Top-level namespaces`
    section."""
    client_py = (nested_generated_base / "client.py").read_text()

    assert "class DocsClientBase:" in client_py
    assert "#### Top-level namespaces" in client_py
    assert "**`commerce`** → `CommerceNamespaceBase`" in client_py


def test_generated_async_client_class_docstring_uses_async_prefix(
    nested_generated_base: Path,
) -> None:
    """The async client class opens with the asynchronous lead."""
    client_py = (nested_generated_base / "client.py").read_text()

    assert "class AsyncDocsClientBase:" in client_py
    assert "Asynchronous HTTP client for `docs-client`" in client_py


def test_generated_namespace_class_docstring_lists_collections(
    nested_generated_base: Path,
) -> None:
    """A namespace class docstring renders a `#### Collections` section listing its children."""
    ns_py = (nested_generated_base / "namespaces" / "commerce.py").read_text()

    assert "class CommerceNamespaceBase:" in ns_py
    assert "#### Collections" in ns_py
    assert "**`orders`** → `OrdersCollectionBase`" in ns_py


def test_generated_collection_class_docstring_includes_item_access_and_operations(
    nested_generated_base: Path,
) -> None:
    """A collection class docstring carries `Item access` and `Operations on the collection`."""
    coll_py = (nested_generated_base / "collections" / "orders.py").read_text()

    assert "#### Item access" in coll_py
    assert "**`collection[id]`** → `OrderResourceBase`" in coll_py
    assert "#### Operations on the collection" in coll_py
    assert "`.create(body)`" in coll_py
    assert "`.first()`" in coll_py


def test_generated_collection_getitem_carries_a_docstring(
    nested_generated_base: Path,
) -> None:
    """`__getitem__` on the generated collection has an IDE-readable docstring."""
    coll_py = (nested_generated_base / "collections" / "orders.py").read_text()

    assert (
        'def __getitem__(self, id: Any) -> OrderResourceBase:\n        """Address one item by `id`'
        in coll_py
    )


def test_generated_resource_class_docstring_lists_crud_section(
    nested_generated_base: Path,
) -> None:
    """A resource class docstring renders an `#### Operations` section with the CRUD
    verbs declared by the spec."""
    res_py = (nested_generated_base / "resources" / "order.py").read_text()

    assert "#### Operations" in res_py
    assert "`.retrieve()`" in res_py
    assert "`.delete()`" in res_py


def test_generated_resource_class_docstring_lists_subcollections_section(
    nested_generated_base: Path,
) -> None:
    """A resource that has a sub-collection lists it in a `#### Sub-collections` section."""
    res_py = (nested_generated_base / "resources" / "order.py").read_text()

    assert "#### Sub-collections" in res_py
    assert "**`lines`** → `OrderLinesCollectionBase`" in res_py


def test_generated_resource_class_docstring_lists_actions_section(
    nested_generated_base: Path,
) -> None:
    """A resource with an action child lists it in an `#### Actions` section."""
    res_py = (nested_generated_base / "resources" / "order.py").read_text()

    assert "#### Actions" in res_py
    assert "**`submit`** → `OrderSubmitActionBase`" in res_py


def test_generated_files_parse_as_python(nested_generated_base: Path) -> None:
    """Every generated `.py` file under `base/` parses cleanly as Python."""
    for path in nested_generated_base.rglob("*.py"):
        ast.parse(path.read_text())


def test_long_description_wraps_within_max_line_length() -> None:
    """A long OpenAPI `description` is reflowed so every emitted line fits 100 chars."""
    long_description = (
        "This endpoint behaves a little differently from the others in this API: "
        "it accepts a free-form filter expression as a query parameter, the "
        "response envelope omits the standard pagination keys, and the resource "
        "returned is augmented with an inline list of related entities so callers "
        "can avoid a follow-up request. The full contract is documented in the "
        "operations guide on the developer portal."
    )

    rendered = build_docstring(
        summary="Run the special query.",
        description=long_description,
        fallback="x",
        indent=4,
    )

    for line in rendered.splitlines():
        assert len(line) <= 100, line


def test_long_bullet_one_line_wraps_with_hanging_indent() -> None:
    """A bullet whose `one_line` is verbose wraps with a hanging indent."""
    refs = [
        ChildRef(
            attr="users",
            class_name="UsersCollectionBase",
            module="users",
            factory_attr="__users_factory__",
            one_line=(
                "List every active user in the directory together with their last "
                "login time, group memberships, and the audit metadata required by "
                "the compliance team."
            ),
        )
    ]

    rendered = build_client_class_docstring(
        project_name="acme",
        project_version=None,
        sync=True,
        top_namespaces=[],
        top_collections=refs,
        top_singletons=[],
        top_actions=[],
    )

    for line in rendered.splitlines():
        assert len(line) <= 100, line
