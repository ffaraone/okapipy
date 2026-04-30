"""Tests for the OpenAPI extension helpers."""

from __future__ import annotations

from okapipy.parser.extension import (
    operation_extension,
    path_item_extension,
    root_namespaces,
)


def test_root_namespaces_returns_declared_paths() -> None:
    """`x-okapipy-ns` at the root is exposed as a set of strings."""
    spec = {"x-okapipy-ns": ["commerce", "settings"]}

    assert root_namespaces(spec) == {"commerce", "settings"}


def test_root_namespaces_returns_empty_when_extension_missing() -> None:
    """A spec without the extension yields an empty set, never None."""
    assert root_namespaces({}) == set()


def test_root_namespaces_ignores_non_string_entries() -> None:
    """Non-string entries inside `x-okapipy-ns` are silently dropped."""
    spec = {"x-okapipy-ns": ["commerce", 42, None]}

    assert root_namespaces(spec) == {"commerce"}


def test_operation_extension_returns_value_when_set() -> None:
    """The string value of `x-okapipy-kind` is returned when present on the operation."""
    operation = {"x-okapipy-kind": "action"}

    assert operation_extension(operation) == "action"


def test_operation_extension_returns_none_when_missing() -> None:
    """No `x-okapipy-kind` key yields None."""
    assert operation_extension({"summary": "x"}) is None


def test_path_item_extension_returns_value_when_set() -> None:
    """Path-item-level `x-okapipy-kind` is read the same way as operation-level."""
    item = {"x-okapipy-kind": "collection"}

    assert path_item_extension(item) == "collection"


def test_root_namespaces_returns_empty_for_non_list_value() -> None:
    """A scalar `x-okapipy-ns` value is rejected as if it were not declared at all."""
    assert root_namespaces({"x-okapipy-ns": "commerce"}) == set()


def test_operation_extension_returns_none_for_non_string_value() -> None:
    """A non-string `x-okapipy-kind` value (e.g. a list) is treated as absent."""
    assert operation_extension({"x-okapipy-kind": ["a"]}) is None
