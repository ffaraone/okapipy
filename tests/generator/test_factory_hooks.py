"""Tests for the dunder-protected `__<child>_factory__` hooks on `*Base` classes.

The factory hook pattern: every `*Base` class that owns a child node declares a
class-level `__<attr>_factory__: ClassVar[type[ChildBase]] = ChildBase` and routes
the property/`__getitem__`/`__call__` accessor through `self.__<attr>_factory__(...)`.
A user-layer subclass swaps the factory by reassigning the class attribute.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "nested.yaml"


@pytest.fixture
def hooks_vfs(tmp_path: Path) -> dict[str, str]:
    """Generate the nested fixture's tree and return the in-memory VFS."""
    api = parse(FIXTURE)
    return generate(
        api,
        raw_spec=FIXTURE,
        output_dir=tmp_path,
        package="hooks",
        client_class="HooksClient",
        project_name="hooks",
    )


@pytest.fixture
def generated_base(tmp_path: Path):
    """Generate the nested fixture, write to disk, import the `base` subpackage."""
    package = "factorycli"
    out = tmp_path / "out"
    api = parse(FIXTURE)
    vfs = generate(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package=package,
        client_class="FactoryClient",
        project_name="factory-client",
    )
    write_to_disk(vfs, out)
    sys.path.insert(0, str(out / "src"))
    try:
        if package in sys.modules:
            del sys.modules[package]
        module = importlib.import_module(f"{package}.base")
        yield module
    finally:
        sys.path.remove(str(out / "src"))
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


def test_client_base_emits_factory_for_top_namespace(hooks_vfs: dict[str, str]) -> None:
    """`ClientBase` declares `__commerce_factory__` typed as `CommerceNamespaceBase`."""
    client_src = hooks_vfs["src/hooks/base/client.py"].content

    assert "__commerce_factory__: ClassVar[type[CommerceNamespaceBase]]" in client_src
    assert "self.__commerce_factory__(self)" in client_src


def test_namespace_base_emits_factory_for_child_collection(hooks_vfs: dict[str, str]) -> None:
    """`CommerceNamespaceBase` declares a `__orders_factory__` ClassVar."""
    namespace_src = hooks_vfs["src/hooks/base/namespaces/commerce.py"].content

    assert "__orders_factory__: ClassVar[type[OrdersCollectionBase]]" in namespace_src
    assert "self.__orders_factory__(client=self.client, path_params={})" in namespace_src


def test_collection_base_routes_getitem_through_resource_factory(
    hooks_vfs: dict[str, str],
) -> None:
    """A collection with a sub-resource routes `__getitem__` through `__resource_factory__`."""
    collection_src = hooks_vfs["src/hooks/base/collections/orders.py"].content

    assert "__resource_factory__: ClassVar[type[OrderResourceBase]]" in collection_src
    assert "self.__resource_factory__(" in collection_src


def test_async_factory_attr_matches_sync(hooks_vfs: dict[str, str]) -> None:
    """The async sibling's factory attribute is typed against the async-base class."""
    namespace_src = hooks_vfs["src/hooks/base/namespaces/commerce.py"].content

    # `Async{Class}Base` carries the same attribute name with the async-typed default.
    assert "__orders_factory__: ClassVar[type[AsyncOrdersCollectionBase]]" in namespace_src
    assert "AsyncOrdersCollectionBase" in namespace_src


def test_resource_emits_factory_for_action_child(hooks_vfs: dict[str, str]) -> None:
    """A resource with an action declares `__<attr>_factory__` for that action."""
    resource_src = hooks_vfs["src/hooks/base/resources/order.py"].content

    assert "__submit_factory__: ClassVar[type[OrderSubmitActionBase]]" in resource_src
    assert "self.__submit_factory__(" in resource_src


def test_user_subclass_can_override_factory_at_runtime(generated_base) -> None:
    """A user-layer subclass that re-binds `__<child>_factory__` swaps the returned type.

    This is the load-bearing customization affordance: a user adds methods to
    their `OrdersCollection(OrdersCollectionBase)` and points `__orders_factory__`
    at it; subsequent `client.commerce.orders` calls return the user's class.
    """
    orders_module = importlib.import_module("factorycli.base.collections.orders")
    OrdersCollectionBase = orders_module.OrdersCollectionBase
    CommerceNamespaceBase = generated_base.CommerceNamespaceBase
    FactoryClientBase = generated_base.FactoryClientBase

    class CustomOrders(OrdersCollectionBase):
        marker = "user-owned"

    class CustomCommerce(CommerceNamespaceBase):
        __orders_factory__ = CustomOrders

    class CustomClient(FactoryClientBase):
        __commerce_factory__ = CustomCommerce

    client = CustomClient("https://api.example.com")
    try:
        commerce = client.commerce
        orders = commerce.orders

        assert isinstance(commerce, CustomCommerce)
        assert isinstance(orders, CustomOrders)
        assert orders.marker == "user-owned"
    finally:
        client.close()


def test_default_factory_returns_base_type(generated_base) -> None:
    """Without any subclass override, accessors return the `*Base` types directly."""
    orders_module = importlib.import_module("factorycli.base.collections.orders")
    FactoryClientBase = generated_base.FactoryClientBase
    CommerceNamespaceBase = generated_base.CommerceNamespaceBase
    OrdersCollectionBase = orders_module.OrdersCollectionBase

    client = FactoryClientBase("https://api.example.com")
    try:
        assert isinstance(client.commerce, CommerceNamespaceBase)
        assert isinstance(client.commerce.orders, OrdersCollectionBase)
    finally:
        client.close()
