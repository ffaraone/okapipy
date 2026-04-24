"""Tests for the list-response resolver: default heuristic and user callback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spacy.language import Language

from okapipy.parser.builder import build, default_list_response_resolver
from okapipy.parser.disambiguation import Sidecar
from okapipy.parser.loader import load_raw_spec, load_spec


def test_default_resolver_extracts_items_from_envelope() -> None:
    """An envelope with one array property is reduced to that array's items schema."""
    schema = {
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "title": "Order"}},
            "total": {"type": "integer"},
        }
    }

    assert default_list_response_resolver(schema)["title"] == "Order"


def test_default_resolver_returns_input_when_no_array_property() -> None:
    """A schema without any array property is returned unchanged."""
    schema = {"properties": {"total": {"type": "integer"}}}

    assert default_list_response_resolver(schema) is schema


def test_default_resolver_returns_input_for_non_dict() -> None:
    """A non-dict schema short-circuits and is returned untouched."""
    assert default_list_response_resolver([]) == []  # type: ignore[arg-type]


def test_user_resolver_overrides_default(
    pagination_spec_path: Path, english_nlp: Language
) -> None:
    """A user resolver that returns a different schema is preferred over the default."""

    def custom(schema: dict[str, Any]) -> dict[str, Any]:
        data = schema.get("properties", {}).get("data")
        return data if isinstance(data, dict) else schema

    spec = load_spec(pagination_spec_path)
    raw = load_raw_spec(pagination_spec_path)

    api = build(spec, raw, Sidecar(), english_nlp, list_response_resolver=custom)

    orders = next(c for c in api.collections if c.name == "Orders")
    assert orders.list_operation is not None
    assert orders.list_operation.response_model == "Order"


def test_user_resolver_returning_unchanged_falls_through_to_default(
    english_nlp: Language,
) -> None:
    """When the user resolver returns the input, the default heuristic still runs."""
    spec = {
        "paths": {
            "/orders": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "properties": {
                                            "items": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "title": "Order",
                                                },
                                            }
                                        }
                                    }
                                }
                            },
                        }
                    }
                }
            }
        }
    }

    def passthrough(schema: dict[str, Any]) -> dict[str, Any]:
        return schema

    api = build(spec, spec, Sidecar(), english_nlp, list_response_resolver=passthrough)

    orders = api.collections[0]
    assert orders.list_operation is not None
    assert orders.list_operation.response_model == "Order"
