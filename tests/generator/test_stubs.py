"""Tests for the user-layer stub emission and the one-shot VFS lifecycle.

Stubs are the customer-editable surface: a `class X(XBase): pass` file per
parser-tree node, plus an empty `__init__.py` and a `client.py` stub. They are
written exactly once on first generation; subsequent regenerations overwrite
the `base/` tree but skip every stub that already exists.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from okapipy.generator import generate_for_mount
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "nested.yaml"


def test_top_level_init_is_emitted_empty_and_one_shot(stubs_vfs) -> None:
    """The user-layer `__init__.py` is empty and never overwritten on regen."""
    init = stubs_vfs["src/acme/client/__init__.py"]

    assert init.content == ""
    assert init.one_shot is True


def test_client_stub_subclasses_client_base(stubs_vfs) -> None:
    """`client.py` carries `class AcmeClient(AcmeClientBase): pass` and the async sibling."""
    client_stub = stubs_vfs["src/acme/client/client.py"]

    assert client_stub.one_shot is True
    assert "class AcmeClient(AcmeClientBase):" in client_stub.content
    assert "class AsyncAcmeClient(AsyncAcmeClientBase):" in client_stub.content
    assert "from acme.client.base.client import" in client_stub.content


def test_namespace_stub_emitted(stubs_vfs) -> None:
    """A namespace produces `class CommerceNamespace(CommerceNamespaceBase): pass`."""
    ns_stub = stubs_vfs["src/acme/client/namespaces/commerce.py"]

    assert ns_stub.one_shot is True
    assert "class CommerceNamespace(CommerceNamespaceBase):" in ns_stub.content
    assert (
        "class AsyncCommerceNamespace(AsyncCommerceNamespaceBase):" in ns_stub.content
    )


def test_collection_stub_emitted(stubs_vfs) -> None:
    """A collection produces `class OrdersCollection(OrdersCollectionBase): pass`."""
    coll_stub = stubs_vfs["src/acme/client/collections/orders.py"]

    assert coll_stub.one_shot is True
    assert "class OrdersCollection(OrdersCollectionBase):" in coll_stub.content


def test_resource_stub_emitted(stubs_vfs) -> None:
    """A resource produces `class OrderResource(OrderResourceBase): pass`."""
    res_stub = stubs_vfs["src/acme/client/resources/order.py"]

    assert res_stub.one_shot is True
    assert "class OrderResource(OrderResourceBase):" in res_stub.content


def test_action_stub_emitted(stubs_vfs) -> None:
    """An action produces `class OrderSubmitAction(OrderSubmitActionBase): pass`."""
    act_stub = stubs_vfs["src/acme/client/actions/order_submit.py"]

    assert act_stub.one_shot is True
    assert "class OrderSubmitAction(OrderSubmitActionBase):" in act_stub.content
    # Actions are leaves — body is `pass`, no factory lines.
    assert "    pass" in act_stub.content


def test_client_stub_auto_wires_top_namespace_factory(stubs_vfs) -> None:
    """The Client stub assigns `__commerce_factory__ = CommerceNamespace` for top namespaces."""
    client_stub = stubs_vfs["src/acme/client/client.py"].content

    assert "__commerce_factory__ = CommerceNamespace" in client_stub
    assert "__commerce_factory__ = AsyncCommerceNamespace" in client_stub
    assert "from acme.client.namespaces.commerce import (" in client_stub


def test_namespace_stub_auto_wires_child_collections(stubs_vfs) -> None:
    """A namespace stub assigns `__orders_factory__ = OrdersCollection` for each child."""
    ns_stub = stubs_vfs["src/acme/client/namespaces/commerce.py"].content

    assert "__orders_factory__ = OrdersCollection" in ns_stub
    assert "__orders_factory__ = AsyncOrdersCollection" in ns_stub
    assert "from acme.client.collections.orders import (" in ns_stub


def test_collection_stub_auto_wires_resource_and_actions(stubs_vfs) -> None:
    """A collection stub wires `__resource_factory__` for its resource."""
    coll_stub = stubs_vfs["src/acme/client/collections/orders.py"].content

    assert "__resource_factory__ = OrderResource" in coll_stub
    assert "__resource_factory__ = AsyncOrderResource" in coll_stub
    assert "from acme.client.resources.order import (" in coll_stub


def test_resource_stub_auto_wires_actions(stubs_vfs) -> None:
    """A resource stub wires `__<action>_factory__` for each action child."""
    res_stub = stubs_vfs["src/acme/client/resources/order.py"].content

    assert "__submit_factory__ = OrderSubmitAction" in res_stub
    assert "__submit_factory__ = AsyncOrderSubmitAction" in res_stub
    assert "from acme.client.actions.order_submit import (" in res_stub


def test_user_subclass_is_on_the_wire_by_default(tmp_path: Path) -> None:
    """Auto-wiring puts the user-layer subclass on the wire without manual edits.

    `client.commerce.orders` must return the user's `OrdersCollection` (not
    `OrdersCollectionBase`) immediately after first generation, with no
    customization applied.
    """
    package = "autowired"
    out = tmp_path / "out"
    api = parse(FIXTURE)
    vfs = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package=package,
        client_class="AutoClient",
    )
    write_to_disk(vfs, out)
    sys.path.insert(0, str(out / "src"))
    try:
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]
        client_module = importlib.import_module(f"{package}.client")
        commerce_module = importlib.import_module(f"{package}.namespaces.commerce")
        orders_module = importlib.import_module(f"{package}.collections.orders")

        client = client_module.AutoClient("https://api.example.com")
        try:
            assert isinstance(client.commerce, commerce_module.CommerceNamespace)
            assert isinstance(client.commerce.orders, orders_module.OrdersCollection)
        finally:
            client.close()
    finally:
        sys.path.remove(str(out / "src"))
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


def test_user_subdir_init_markers_are_one_shot_empty(stubs_vfs) -> None:
    """Each populated user-layer subdir gets an empty `__init__.py` marker."""
    for subdir in ("namespaces", "collections", "resources", "actions"):
        marker = stubs_vfs[f"src/acme/client/{subdir}/__init__.py"]
        assert marker.content == ""
        assert marker.one_shot is True


def test_base_files_are_regenerated_not_one_shot(stubs_vfs) -> None:
    """The regenerated tree under `base/` carries `one_shot=False`."""
    base_init = stubs_vfs["src/acme/client/base/__init__.py"]
    base_client = stubs_vfs["src/acme/client/base/client.py"]
    base_models = stubs_vfs["src/acme/client/base/models.py"]

    assert base_init.one_shot is False
    assert base_client.one_shot is False
    assert base_models.one_shot is False


def test_project_skeleton_is_one_shot(stubs_vfs) -> None:
    """`pyproject.toml`, `README.md`, `LICENSE`, etc. are one-shot.

    This protects customer edits to dependency lists or project metadata
    across regenerations — a latent regression in the pre-Phase-3 generator
    that this lifecycle change resolves.
    """
    for path in (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        ".gitignore",
        ".python-version",
    ):
        assert stubs_vfs[path].one_shot is True, f"{path} should be one-shot"


def test_write_to_disk_skips_existing_one_shot_files(tmp_path: Path) -> None:
    """A pre-existing user-layer file is preserved verbatim on regeneration."""
    api = parse(FIXTURE)
    out = tmp_path / "out"

    # First generation: write everything.
    vfs1 = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package="rerun",
        client_class="Rerun",
    )
    write_to_disk(vfs1, out)

    # Customer edits a stub.
    customized = out / "src" / "rerun" / "collections" / "orders.py"
    custom_marker = "# CUSTOMER-OWNED PAYLOAD"
    customized.write_text(custom_marker, encoding="utf-8")

    # Second generation: re-run.
    vfs2 = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package="rerun",
        client_class="Rerun",
    )
    write_to_disk(vfs2, out)

    # Customer file untouched.
    assert customized.read_text(encoding="utf-8") == custom_marker


def test_write_to_disk_overwrites_base_files(tmp_path: Path) -> None:
    """A regenerated base file is overwritten on every run."""
    api = parse(FIXTURE)
    out = tmp_path / "out"

    vfs1 = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package="rerun2",
        client_class="Rerun2",
    )
    write_to_disk(vfs1, out)

    base_file = out / "src" / "rerun2" / "base" / "collections" / "orders.py"
    base_file.write_text("# stale", encoding="utf-8")

    vfs2 = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package="rerun2",
        client_class="Rerun2",
    )
    write_to_disk(vfs2, out)

    assert base_file.read_text(encoding="utf-8") != "# stale"
    assert "OrdersCollectionBase" in base_file.read_text(encoding="utf-8")


def test_user_subclass_is_a_runtime_drop_in(tmp_path: Path) -> None:
    """The user-layer subclass instantiates and behaves like the base class.

    Imports the user stub `<pkg>.client.AcmeClient` (not the base) and verifies
    the chain `client.commerce.orders` walks through the user-layer types.
    """
    package = "stubsdrop"
    out = tmp_path / "out"
    api = parse(FIXTURE)
    vfs = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package=package,
        client_class="DropClient",
    )
    write_to_disk(vfs, out)
    sys.path.insert(0, str(out / "src"))
    try:
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]
        client_module = importlib.import_module(f"{package}.client")
        DropClient = client_module.DropClient
        commerce_ns_module = importlib.import_module(f"{package}.namespaces.commerce")
        CommerceNamespace = commerce_ns_module.CommerceNamespace
        orders_module = importlib.import_module(f"{package}.collections.orders")
        OrdersCollection = orders_module.OrdersCollection

        client = DropClient("https://api.example.com")
        try:
            assert isinstance(client.commerce, CommerceNamespace.__bases__[0])
            assert isinstance(client.commerce.orders, OrdersCollection.__bases__[0])
        finally:
            client.close()
    finally:
        sys.path.remove(str(out / "src"))
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


def test_new_collection_added_between_runs_creates_new_stub(tmp_path: Path) -> None:
    """A spec that grows a new collection produces a stub for it on the next run."""
    package = "growth"
    out = tmp_path / "out"

    # First generation: run with the simple fixture (single collection).
    simple = Path(__file__).resolve().parent.parent / "fixtures" / "simple.yaml"
    api1 = parse(simple)
    vfs1 = generate_for_mount(
        api1,
        raw_spec=simple,
        output_dir=out,
        package=package,
        client_class="GrowClient",
    )
    write_to_disk(vfs1, out)

    pre_existing = list((out / "src" / package / "collections").glob("*.py"))

    # Second generation: switch to the nested fixture (more collections).
    api2 = parse(FIXTURE)
    vfs2 = generate_for_mount(
        api2,
        raw_spec=FIXTURE,
        output_dir=out,
        package=package,
        client_class="GrowClient",
    )
    write_to_disk(vfs2, out)

    after = list((out / "src" / package / "collections").glob("*.py"))

    assert len(after) > len(pre_existing)
