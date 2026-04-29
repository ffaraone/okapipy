"""Composable sort expressions.

`Sort` holds a list of `(field, direction)` terms. Composition via `+` appends;
unary `-` flips every direction; `Sort("-field")` is shorthand for descending.
Strategies (`CommaSignedSort`, `KeyDirectionSort`, `JsonApiSort`) walk the term
list and emit the wire encoding their API expects.
"""

from __future__ import annotations

from typing import Literal

Direction = Literal["asc", "desc"]


class Sort:
    """One or more sort terms. Composable via `+` and unary `-`.

    `Sort("created_at")` ascending. `Sort("-created_at")` descending (the leading
    `-` is a shorthand). `-Sort("created_at")` flips direction. Adding two `Sort`
    instances concatenates their term lists; the empty `Sort()` is a valid neutral
    element.
    """

    def __init__(self, field: str | None = None, direction: Direction = "asc") -> None:
        self.terms: list[tuple[str, Direction]] = []
        if field is not None:
            if field.startswith("-"):
                field = field[1:]
                direction = "desc"
            elif field.startswith("+"):
                field = field[1:]
            self.terms.append((field, direction))

    def __add__(self, other: Sort) -> Sort:
        result = Sort()
        result.terms = [*self.terms, *other.terms]
        return result

    def __neg__(self) -> Sort:
        flipped: Direction
        result = Sort()
        for field, direction in self.terms:
            flipped = "desc" if direction == "asc" else "asc"
            result.terms.append((field, flipped))
        return result

    def __repr__(self) -> str:
        rendered = ", ".join(
            f"{'-' if d == 'desc' else ''}{f}" for f, d in self.terms
        )
        return f"Sort({rendered})"

    def __bool__(self) -> bool:
        return bool(self.terms)
