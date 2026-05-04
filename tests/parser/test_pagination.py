"""Tests for the pagination/filter/sort capability flags, response_headers capture,
and envelope semantics."""

from __future__ import annotations

from pathlib import Path

from spacy.language import Language

from okapipy.parser.builder import build
from okapipy.parser.rules import Rules, load_rules


def test_collection_fetch_is_paginated_by_default(english_nlp: Language) -> None:
    """A bare collection fetch with no extension defaults to `pagination_supported=True`."""
    spec = {
        "paths": {
            "/orders": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.pagination_supported is True


def test_filter_and_sort_default_to_unsupported(english_nlp: Language) -> None:
    """`filter_supported` and `sort_supported` default to False on every operation.

    Future `x-okapipy-filter` and `x-okapipy-sort` extensions will flip them on,
    but with no extension present the parser must report the capability as absent
    so the generator omits the corresponding fluent methods.
    """
    spec = {
        "paths": {
            "/orders": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    fetch = api.collections[0].fetch
    assert fetch is not None
    assert fetch.filter_supported is False
    assert fetch.sort_supported is False


def test_path_item_extension_disables_pagination(english_nlp: Language) -> None:
    """`x-okapipy-paginated: false` at the path-item level cascades to all methods."""
    spec = {
        "paths": {
            "/orders": {
                "x-okapipy-paginated": False,
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.pagination_supported is False


def test_operation_extension_overrides_path_item(english_nlp: Language) -> None:
    """A per-operation `x-okapipy-paginated` overrides the path-item-level value."""
    spec = {
        "paths": {
            "/orders": {
                "x-okapipy-paginated": False,
                "get": {
                    "x-okapipy-paginated": True,
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.pagination_supported is True


def test_rules_paginated_wins_over_spec(english_nlp: Language, tmp_path: Path) -> None:
    """When rules and spec both set `x-okapipy-paginated`, rules win."""
    rules_file = tmp_path / "side.yaml"
    rules_file.write_text("paths:\n  /orders:\n    x-okapipy-paginated: false\n")
    spec = {
        "paths": {
            "/orders": {
                "x-okapipy-paginated": True,
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, load_rules(rules_file), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.pagination_supported is False


def test_rules_per_method_paginated_overrides_path_item(
    english_nlp: Language, tmp_path: Path
) -> None:
    """A rules-file per-method `x-okapipy-paginated` overrides its rules path-item value."""
    rules_file = tmp_path / "side.yaml"
    rules_file.write_text(
        "paths:\n"
        "  /orders:\n"
        "    x-okapipy-paginated: false\n"
        "    get:\n"
        "      x-okapipy-paginated: true\n"
    )
    spec = {
        "paths": {
            "/orders": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, load_rules(rules_file), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.pagination_supported is True


def test_response_headers_are_captured(english_nlp: Language) -> None:
    """Header names declared on the chosen 2xx response are surfaced on Operation."""
    spec = {
        "paths": {
            "/orders": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "headers": {
                                "Link": {"schema": {"type": "string"}},
                                "X-Total-Count": {"schema": {"type": "integer"}},
                            },
                        }
                    }
                }
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.response_headers == ["Link", "X-Total-Count"]


def test_response_model_names_the_envelope_not_the_item(english_nlp: Language) -> None:
    """`response_model` points at the literal response schema, never at items."""
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
                                        "$ref": "#/components/schemas/OrderListResponse"
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "OrderListResponse": {
                    "type": "object",
                    "title": "OrderListResponse",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {"type": "object", "title": "Order"},
                        },
                        "total": {"type": "integer"},
                    },
                }
            }
        },
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is not None
    assert orders.fetch.response_model == "OrderListResponse"


def test_paginated_flag_is_true_even_for_resource_get(english_nlp: Language) -> None:
    """Resource GETs default to `paginated=True` too — the flag is inert for non-list ops.

    The generator decides where to honor it; the parser just records the flag.
    """
    spec = {
        "paths": {
            "/orders/{id}": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    resource = api.collections[0].resource
    assert resource is not None
    assert resource.retrieve is not None
    assert resource.retrieve.pagination_supported is True
