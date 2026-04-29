"""Composition algebra and tree-walk helpers for `Filter`."""

from __future__ import annotations

import pytest

from okapipy.generator.runtime.filters import (
    AndFilter,
    Filter,
    NotFilter,
    OrFilter,
    Search,
)


def test_filter_constructs_with_kwargs() -> None:
    """`Filter(**kwargs)` stores the kwargs verbatim — strategies read them later."""
    f = Filter(status="open", customer_id=42)

    assert f.kwargs == {"status": "open", "customer_id": 42}


def test_and_or_not_compose_into_internal_nodes() -> None:
    """`&`, `|`, `~` produce `AndFilter`, `OrFilter`, `NotFilter` wrapping the operands."""
    a = Filter(status="open")
    b = Filter(customer_id=42)

    combined = a & b
    union = a | b
    negated = ~a

    assert isinstance(combined, AndFilter)
    assert combined.left is a
    assert combined.right is b
    assert isinstance(union, OrFilter)
    assert isinstance(negated, NotFilter)
    assert negated.operand is a


def test_iter_leaves_visits_only_leaves() -> None:
    """`iter_leaves()` walks the tree and yields non-composition nodes."""
    a = Filter(status="open")
    b = Filter(customer_id=42)
    c = Filter(priority="high")
    tree = (a & b) | ~c

    leaves = list(tree.iter_leaves())

    assert leaves == [a, b, c]


def test_iter_leaves_filters_by_type() -> None:
    """`iter_leaves(of_type=...)` yields only leaves matching the requested class."""
    plain = Filter(status="open")
    text = Search("running shoes")
    tree = plain & text

    found = list(tree.iter_leaves(Search))

    assert found == [text]


def test_without_drops_a_subclass_and_collapses_empty_branches() -> None:
    """`without(of_type)` removes leaves of the given subclass; empty branches collapse."""
    plain = Filter(status="open")
    text = Search("hello")
    tree = plain & text

    pruned = tree.without(Search)

    assert pruned is plain  # only `plain` remains, no AndFilter wrapper


def test_without_returns_none_when_every_leaf_is_pruned() -> None:
    """`without(of_type)` returns `None` when every leaf was an instance of the type."""
    text = Search("hello") | Search("world")

    pruned = text.without(Search)

    assert pruned is None


def test_subclass_can_carry_extra_attributes() -> None:
    """Subclassing `Filter` is the user-facing extension hook for novel leaves."""

    class GeoFilter(Filter):
        def __init__(self, *, within_box: tuple[float, float, float, float]) -> None:
            super().__init__()
            self.within_box = within_box

    geo = GeoFilter(within_box=(0.0, 0.0, 1.0, 1.0))
    plain = Filter(category="restaurant")
    tree = geo & plain

    geo_leaves = list(tree.iter_leaves(GeoFilter))
    assert geo_leaves == [geo]
    assert geo_leaves[0].within_box == (0.0, 0.0, 1.0, 1.0)


@pytest.mark.parametrize("op", ["and", "or"])
def test_repr_renders_composition_for_debugging(op: str) -> None:
    """Filter `__repr__` is composition-aware so debug logs read sensibly."""
    a = Filter(status="open")
    b = Filter(customer_id=42)
    tree = (a & b) if op == "and" else (a | b)

    rendered = repr(tree)

    assert "Filter(status='open')" in rendered
    assert "Filter(customer_id=42)" in rendered
