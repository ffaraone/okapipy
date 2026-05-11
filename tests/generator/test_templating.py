"""Unit tests for the public casing helpers in the generator's templating module."""

from __future__ import annotations

import pytest

from okapipy.generator.templating import snake_case


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Order", "order"),
        ("OrderLine", "order_line"),
        ("password-recovery-requests", "password_recovery_requests"),
        ("HTMLParser", "html_parser"),
    ],
)
def test_snake_case_handles_standard_casing(value: str, expected: str) -> None:
    """`snake_case` lowercases PascalCase, camelCase, and kebab-case input."""
    assert snake_case(value) == expected


def test_snake_case_expands_leading_dot_to_dot_token() -> None:
    """A segment like `.well-known` becomes `dot_well_known` rather than `.well_known`."""
    assert snake_case(".well-known") == "dot_well_known"


def test_snake_case_expands_embedded_dot_to_dot_token() -> None:
    """An embedded `.` (e.g. `api.v1`) becomes the word `dot` between parts."""
    assert snake_case("api.v1") == "api_dot_v1"


def test_snake_case_result_is_a_valid_python_identifier() -> None:
    """`snake_case` of a `.well-known`-style segment yields a valid Python identifier.

    The generator emits modules and attributes named from path segments; if the
    result is not a valid identifier the generated package fails to import.
    """
    assert snake_case(".well-known").isidentifier()
