"""Tests for the rules loader and lookup helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from okapipy.parser.errors import RulesFormatError
from okapipy.parser.rules import (
    Rules,
    extra_namespaces,
    load_rules,
    operation_hint,
    path_item_hint,
)


def test_load_rules_returns_empty_when_source_is_none() -> None:
    """Passing None yields an empty rules without touching the filesystem."""
    rules = load_rules(None)

    assert rules.x_okapipy_ns == []
    assert rules.paths == {}


def test_load_rules_parses_yaml_root_extensions(rules_path: Path) -> None:
    """Root `x-okapipy-ns` is mapped onto the rules's namespace list."""
    rules = load_rules(rules_path)

    assert rules.x_okapipy_ns == ["commerce", "settings"]


def test_load_rules_parses_per_operation_hint(rules_path: Path) -> None:
    """Per-method `x-okapipy-kind` is exposed via operation_hint."""
    rules = load_rules(rules_path)

    assert operation_hint(rules, "/commerce/orders/{id}/submit", "POST") == "action"


def test_load_rules_parses_path_item_hint(rules_path: Path) -> None:
    """Path-item-level `x-okapipy-kind` is exposed via path_item_hint."""
    rules = load_rules(rules_path)

    assert path_item_hint(rules, "/commerce/staff") == "collection"


def test_extra_namespaces_returns_a_set(rules_path: Path) -> None:
    """The namespace registry is exposed as a set, not a list."""
    rules = load_rules(rules_path)

    assert extra_namespaces(rules) == {"commerce", "settings"}


def test_load_rules_accepts_json_input(tmp_path: Path) -> None:
    """JSON-encoded rules files are parsed via the JSON branch first."""
    target = tmp_path / "rules.json"
    target.write_text('{"x-okapipy-ns": ["a"], "paths": {}}')

    rules = load_rules(target)

    assert rules.x_okapipy_ns == ["a"]


def test_load_rules_rejects_unknown_hint(tmp_path: Path) -> None:
    """`x-okapipy-kind` values outside the four legal kinds are flagged early."""
    target = tmp_path / "rules.yaml"
    target.write_text("paths:\n  /x:\n    x-okapipy-kind: garbage\n")

    with pytest.raises(RulesFormatError, match="garbage"):
        load_rules(target)


def test_load_rules_rejects_non_mapping_root(tmp_path: Path) -> None:
    """A YAML/JSON document whose root is a scalar or list is rejected."""
    target = tmp_path / "rules.yaml"
    target.write_text("- a\n- b\n")

    with pytest.raises(RulesFormatError, match="mapping"):
        load_rules(target)


def test_load_rules_missing_file_raises(tmp_path: Path) -> None:
    """A non-existent path is wrapped as a RulesFormatError."""
    with pytest.raises(RulesFormatError, match="failed to read rules"):
        load_rules(tmp_path / "missing.yaml")


def test_operation_hint_falls_back_to_path_item_when_method_absent() -> None:
    """When the per-method entry is missing the path-item hint is returned."""
    rules = Rules.model_validate({"paths": {"/x": {"x-okapipy-kind": "collection"}}})

    assert operation_hint(rules, "/x", "PATCH") == "collection"


def test_operation_hint_returns_none_for_unknown_path() -> None:
    """Looking up a path the rules does not declare yields None."""
    assert operation_hint(Rules(), "/anything", "GET") is None


def test_load_rules_rejects_invalid_yaml(tmp_path: Path) -> None:
    """A file that is neither valid JSON nor valid YAML raises RulesFormatError."""
    target = tmp_path / "bad.yaml"
    target.write_text("paths:\n  /x:\n    -invalid: : :\n  unbalanced:[\n")

    with pytest.raises(RulesFormatError, match="not valid JSON or YAML"):
        load_rules(target)


def test_load_rules_rejects_unknown_per_method_hint(tmp_path: Path) -> None:
    """A garbage `x-okapipy-kind` value at method level is flagged with method context."""
    target = tmp_path / "side.yaml"
    target.write_text("paths:\n  /x:\n    post:\n      x-okapipy-kind: bogus\n")

    with pytest.raises(RulesFormatError, match="post"):
        load_rules(target)


def test_load_rules_accepts_singleton_hint(tmp_path: Path) -> None:
    """`singleton` is a legal `x-okapipy-kind` value at both path and operation level."""
    target = tmp_path / "rules.yaml"
    target.write_text(
        "paths:\n"
        "  /me:\n"
        "    x-okapipy-kind: singleton\n"
        "  /users/{id}/avatar:\n"
        "    get:\n"
        "      x-okapipy-kind: singleton\n"
    )

    rules = load_rules(target)

    assert path_item_hint(rules, "/me") == "singleton"
    assert operation_hint(rules, "/users/{id}/avatar", "GET") == "singleton"
