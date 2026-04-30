"""End-to-end smoke tests for the public `parse` entry point."""

from __future__ import annotations

from pathlib import Path

from okapipy.parser import parse


def test_parse_simple_spec_returns_top_level_collection(
    simple_spec_path: Path, tmp_path: Path
) -> None:
    """A two-path spec yields one root collection whose resource is named `Order`."""
    cache_dir = Path(__file__).resolve().parent.parent.parent / ".spacy"

    api = parse(simple_spec_path, nlp_cache_dir=cache_dir)

    orders = next(c for c in api.collections if c.name == "Orders")
    assert orders.fetch is not None
    assert orders.resource is not None
    assert orders.resource.name == "Order"


def test_parse_nested_spec_with_rules_namespace(
    nested_spec_path: Path, rules_path: Path
) -> None:
    """A rules-file's `x-okapipy-ns` is honored alongside the spec's own registry."""
    cache_dir = Path(__file__).resolve().parent.parent.parent / ".spacy"

    api = parse(nested_spec_path, rules=rules_path, nlp_cache_dir=cache_dir)

    commerce = next(ns for ns in api.namespaces if ns.name == "commerce")
    orders = next(c for c in commerce.collections if c.name == "Orders")
    assert orders.resource is not None
    assert any(action.name == "OrderSubmit" for action in orders.resource.actions)
