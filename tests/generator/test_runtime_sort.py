"""Composition algebra for `Sort`."""

from __future__ import annotations

from okapipy.generator.runtime.sort import Sort


def test_single_term_construction() -> None:
    """`Sort(field)` stores one ascending term."""
    s = Sort("created_at")

    assert s.terms == [("created_at", "asc")]


def test_minus_prefix_means_descending() -> None:
    """`Sort('-field')` is shorthand for descending; the leading `-` is stripped."""
    s = Sort("-created_at")

    assert s.terms == [("created_at", "desc")]


def test_unary_minus_flips_every_direction() -> None:
    """`-Sort(...)` flips asc↔desc on every term in the chain."""
    s = -(Sort("created_at") + Sort("-id"))

    assert s.terms == [("created_at", "desc"), ("id", "asc")]


def test_addition_concatenates_terms() -> None:
    """`Sort('a') + Sort('b')` produces the two-term list in order."""
    s = Sort("created_at") + Sort("-id")

    assert s.terms == [("created_at", "asc"), ("id", "desc")]


def test_empty_sort_is_falsy() -> None:
    """An empty `Sort()` is falsy, so strategies can short-circuit on `if not s`."""
    assert not Sort()
    assert Sort("created_at")


def test_repr_round_trips_signs() -> None:
    """`__repr__` shows leading `-` for descending terms — easy to read in logs."""
    s = Sort("-created_at") + Sort("id")

    assert repr(s) == "Sort(-created_at, id)"
