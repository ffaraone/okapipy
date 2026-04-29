"""Composable filter expressions.

`Filter` is the base class. Construct it directly with kwargs (Django-Q style)
for the common case, or subclass for novel leaves (geo, JSON expressions,
RSQL ASTs, etc.). Composition operators `&`, `|`, `~` build internal
`AndFilter` / `OrFilter` / `NotFilter` nodes; concrete strategies walk the
resulting tree at request time and produce wire parameters.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class Filter:
    """A filter expression. Subclassable for novel node types.

    Default construction takes `**kwargs` and stores them on `self.kwargs`. Strategies
    inspect both `type(node)` and `node.kwargs` when encoding. Composition produces
    `AndFilter`, `OrFilter`, `NotFilter` — internal nodes that wrap operands.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs: dict[str, Any] = dict(kwargs)

    def __and__(self, other: Filter) -> Filter:
        return AndFilter(self, other)

    def __or__(self, other: Filter) -> Filter:
        return OrFilter(self, other)

    def __invert__(self) -> Filter:
        return NotFilter(self)

    def __repr__(self) -> str:
        kwargs = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        return f"{type(self).__name__}({kwargs})"

    def iter_leaves(self, of_type: type[Filter] | None = None) -> Iterator[Filter]:
        """Yield leaf filters in tree order, optionally restricted to `of_type`.

        Internal nodes (`AndFilter` / `OrFilter` / `NotFilter`) recurse; leaves
        either yield themselves (if matching) or skip.
        """
        if isinstance(self, (AndFilter, OrFilter)):
            yield from self.left.iter_leaves(of_type)
            yield from self.right.iter_leaves(of_type)
            return
        if isinstance(self, NotFilter):
            yield from self.operand.iter_leaves(of_type)
            return
        if of_type is None or isinstance(self, of_type):
            yield self

    def without(self, of_type: type[Filter]) -> Filter | None:
        """Return a copy of this tree with all leaves of `of_type` removed.

        Returns `None` if the result is empty (every leaf was an instance).
        """
        if isinstance(self, AndFilter):
            left = self.left.without(of_type)
            right = self.right.without(of_type)
            return _combine(left, right, AndFilter)
        if isinstance(self, OrFilter):
            left = self.left.without(of_type)
            right = self.right.without(of_type)
            return _combine(left, right, OrFilter)
        if isinstance(self, NotFilter):
            inner = self.operand.without(of_type)
            return None if inner is None else NotFilter(inner)
        if isinstance(self, of_type):
            return None
        return self


class AndFilter(Filter):
    """Conjunction of two filter expressions."""

    def __init__(self, left: Filter, right: Filter) -> None:
        super().__init__()
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"({self.left!r} & {self.right!r})"


class OrFilter(Filter):
    """Disjunction of two filter expressions."""

    def __init__(self, left: Filter, right: Filter) -> None:
        super().__init__()
        self.left = left
        self.right = right

    def __repr__(self) -> str:
        return f"({self.left!r} | {self.right!r})"


class NotFilter(Filter):
    """Negation of a filter expression."""

    def __init__(self, operand: Filter) -> None:
        super().__init__()
        self.operand = operand

    def __repr__(self) -> str:
        return f"~{self.operand!r}"


class Search(Filter):
    """Free-text query leaf used by search-style APIs (`?q=…`).

    Construct as `Search("running shoes")`. The configured `SearchFilterStrategy`
    pulls `self.query` and emits the configured query parameter.
    """

    def __init__(self, query: str) -> None:
        super().__init__(q=query)
        self.query = query


def _combine(
    left: Filter | None,
    right: Filter | None,
    kind: type[AndFilter] | type[OrFilter],
) -> Filter | None:
    """Reassemble a binary node, dropping a side that pruned to None."""
    if left is None and right is None:
        return None
    if left is None:
        return right
    if right is None:
        return left
    return kind(left, right)
