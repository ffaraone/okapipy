"""Tests for the sidecar loader and lookup helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from okapipy.parser.disambiguation import (
    Sidecar,
    extra_namespaces,
    load_sidecar,
    operation_hint,
    path_item_hint,
)
from okapipy.parser.errors import SidecarFormatError


def test_load_sidecar_returns_empty_when_source_is_none() -> None:
    """Passing None yields an empty sidecar without touching the filesystem."""
    sidecar = load_sidecar(None)

    assert sidecar.x_okapipy_ns == []
    assert sidecar.paths == {}


def test_load_sidecar_parses_yaml_root_extensions(sidecar_path: Path) -> None:
    """Root `x-okapipy-ns` is mapped onto the sidecar's namespace list."""
    sidecar = load_sidecar(sidecar_path)

    assert sidecar.x_okapipy_ns == ["commerce", "settings"]


def test_load_sidecar_parses_per_operation_hint(sidecar_path: Path) -> None:
    """Per-method `x-okapipy` is exposed via operation_hint."""
    sidecar = load_sidecar(sidecar_path)

    assert operation_hint(sidecar, "/commerce/orders/{id}/submit", "POST") == "action"


def test_load_sidecar_parses_path_item_hint(sidecar_path: Path) -> None:
    """Path-item-level `x-okapipy` is exposed via path_item_hint."""
    sidecar = load_sidecar(sidecar_path)

    assert path_item_hint(sidecar, "/commerce/staff") == "collection"


def test_extra_namespaces_returns_a_set(sidecar_path: Path) -> None:
    """The namespace registry is exposed as a set, not a list."""
    sidecar = load_sidecar(sidecar_path)

    assert extra_namespaces(sidecar) == {"commerce", "settings"}


def test_load_sidecar_accepts_json_input(tmp_path: Path) -> None:
    """JSON-encoded sidecars are parsed via the JSON branch first."""
    target = tmp_path / "sidecar.json"
    target.write_text('{"x-okapipy-ns": ["a"], "paths": {}}')

    sidecar = load_sidecar(target)

    assert sidecar.x_okapipy_ns == ["a"]


def test_load_sidecar_rejects_unknown_hint(tmp_path: Path) -> None:
    """`x-okapipy` values outside the four legal kinds are flagged early."""
    target = tmp_path / "sidecar.yaml"
    target.write_text("paths:\n  /x:\n    x-okapipy: garbage\n")

    with pytest.raises(SidecarFormatError, match="garbage"):
        load_sidecar(target)


def test_load_sidecar_rejects_non_mapping_root(tmp_path: Path) -> None:
    """A YAML/JSON document whose root is a scalar or list is rejected."""
    target = tmp_path / "sidecar.yaml"
    target.write_text("- a\n- b\n")

    with pytest.raises(SidecarFormatError, match="mapping"):
        load_sidecar(target)


def test_load_sidecar_missing_file_raises(tmp_path: Path) -> None:
    """A non-existent path is wrapped as a SidecarFormatError."""
    with pytest.raises(SidecarFormatError):
        load_sidecar(tmp_path / "missing.yaml")


def test_operation_hint_falls_back_to_path_item_when_method_absent() -> None:
    """When the per-method entry is missing the path-item hint is returned."""
    sidecar = Sidecar.model_validate({"paths": {"/x": {"x-okapipy": "collection"}}})

    assert operation_hint(sidecar, "/x", "PATCH") == "collection"


def test_operation_hint_returns_none_for_unknown_path() -> None:
    """Looking up a path the sidecar does not declare yields None."""
    assert operation_hint(Sidecar(), "/anything", "GET") is None


def test_load_sidecar_rejects_invalid_yaml(tmp_path: Path) -> None:
    """A file that is neither valid JSON nor valid YAML raises SidecarFormatError."""
    target = tmp_path / "bad.yaml"
    target.write_text("paths:\n  /x:\n    -invalid: : :\n  unbalanced:[\n")

    with pytest.raises(SidecarFormatError):
        load_sidecar(target)


def test_load_sidecar_rejects_unknown_per_method_hint(tmp_path: Path) -> None:
    """A garbage `x-okapipy` value at method level is flagged with method context."""
    target = tmp_path / "side.yaml"
    target.write_text("paths:\n  /x:\n    post:\n      x-okapipy: bogus\n")

    with pytest.raises(SidecarFormatError, match="post"):
        load_sidecar(target)
