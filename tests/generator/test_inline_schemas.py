"""Tests for the inline-schema flattening preprocessor."""

from __future__ import annotations

from typing import Any

from okapipy.generator.inline_schemas import flatten_inline_schemas


def _empty_spec() -> dict[str, Any]:
    """Return a minimal OpenAPI 3 envelope with empty paths/components."""
    return {"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {}}


def test_inline_object_with_properties_is_hoisted_to_components() -> None:
    """An inline object schema is moved into components.schemas and replaced by a $ref."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {
                    "by": {"type": "object", "properties": {"id": {"type": "string"}}},
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    assert flat["components"]["schemas"]["Order"]["properties"]["by"] == {
        "$ref": "#/components/schemas/By",
    }
    assert flat["components"]["schemas"]["By"]["properties"]["id"] == {"type": "string"}


def test_structurally_identical_inline_schemas_collapse_to_one_component() -> None:
    """Three inline `by` fields with the same shape produce a single shared component."""
    inline_actor = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
    }
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Created": {
                "type": "object",
                "properties": {"by": dict(inline_actor)},
            },
            "Updated": {
                "type": "object",
                "properties": {"by": dict(inline_actor)},
            },
            "Deleted": {
                "type": "object",
                "properties": {"by": dict(inline_actor)},
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    schemas = flat["components"]["schemas"]
    assert sum(1 for k in schemas if k.lower().startswith("by")) == 1
    for parent in ("Created", "Updated", "Deleted"):
        assert schemas[parent]["properties"]["by"] == {"$ref": "#/components/schemas/By"}


def test_title_wins_over_breadcrumb_for_extracted_name() -> None:
    """When an inline schema carries a `title`, the title is used as its component name."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {
                    "owner": {
                        "title": "Actor",
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    assert "Actor" in flat["components"]["schemas"]
    assert flat["components"]["schemas"]["Order"]["properties"]["owner"] == {
        "$ref": "#/components/schemas/Actor",
    }


def test_distinct_shapes_with_same_property_name_get_qualified_names() -> None:
    """Two `by` fields with different shapes yield `By` and `<Parent>By` — no hash suffixes."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Created": {
                "type": "object",
                "properties": {
                    "by": {"type": "object", "properties": {"id": {"type": "string"}}},
                },
            },
            "Audit": {
                "type": "object",
                "properties": {
                    "by": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "ip": {"type": "string"}},
                    },
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    schemas = flat["components"]["schemas"]
    refs = {
        schemas["Created"]["properties"]["by"]["$ref"],
        schemas["Audit"]["properties"]["by"]["$ref"],
    }
    assert refs == {"#/components/schemas/By", "#/components/schemas/AuditBy"}


def test_inline_enum_is_hoisted_to_named_component() -> None:
    """Inline string enums become their own components, deduped across occurrences."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Owner": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["user", "system"]},
                },
            },
            "Reviewer": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["user", "system"]},
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    schemas = flat["components"]["schemas"]
    assert schemas["Owner"]["properties"]["kind"] == schemas["Reviewer"]["properties"]["kind"]
    assert schemas["Owner"]["properties"]["kind"]["$ref"].startswith("#/components/schemas/")


def test_existing_top_level_components_are_left_alone() -> None:
    """Top-level component names stay even when an inline schema would collide on name."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "By": {
                "type": "object",
                "properties": {"reserved": {"type": "string"}},
            },
            "Created": {
                "type": "object",
                "properties": {
                    "by": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    schemas = flat["components"]["schemas"]
    assert schemas["By"]["properties"] == {"reserved": {"type": "string"}}
    new_ref = schemas["Created"]["properties"]["by"]["$ref"]
    assert new_ref != "#/components/schemas/By"
    assert new_ref.startswith("#/components/schemas/")


def test_request_body_inline_object_is_extracted() -> None:
    """An inline schema in a requestBody slot is extracted just like nested properties."""
    spec = _empty_spec()
    spec["paths"] = {
        "/widgets": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "title": "WidgetCreate",
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                            },
                        },
                    },
                },
                "responses": {"200": {"description": "ok"}},
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    body_schema = flat["paths"]["/widgets"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert body_schema == {"$ref": "#/components/schemas/WidgetCreate"}
    assert flat["components"]["schemas"]["WidgetCreate"]["properties"] == {
        "name": {"type": "string"},
    }


def test_response_inline_object_is_extracted() -> None:
    """An inline schema in a response body is hoisted to components."""
    spec = _empty_spec()
    spec["paths"] = {
        "/widgets": {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "title": "WidgetList",
                                    "type": "object",
                                    "properties": {"count": {"type": "integer"}},
                                },
                            },
                        },
                    },
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    resp_schema = flat["paths"]["/widgets"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert resp_schema == {"$ref": "#/components/schemas/WidgetList"}


def test_array_items_inline_object_is_extracted() -> None:
    """Inline schemas under `items` are extracted just like under `properties`."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "WidgetList": {
                "type": "array",
                "items": {
                    "title": "Widget",
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    assert flat["components"]["schemas"]["WidgetList"]["items"] == {
        "$ref": "#/components/schemas/Widget",
    }


def test_primitive_schemas_are_not_extracted() -> None:
    """Bare primitive schemas (string/integer with no enum) stay inline — extracting
    them would produce nameless aliases that dmcg can't usefully model."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "qty": {"type": "integer"},
                },
            },
        },
    }

    before = flatten_inline_schemas(spec)
    schemas = before["components"]["schemas"]
    # Only Order remains; primitives stay inline.
    assert set(schemas.keys()) == {"Order"}
    assert schemas["Order"]["properties"]["name"] == {"type": "string"}


def test_input_spec_is_not_mutated() -> None:
    """`flatten_inline_schemas` returns a deep copy and leaves the caller's dict unchanged."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {
                    "by": {"type": "object", "properties": {"id": {"type": "string"}}},
                },
            },
        },
    }
    snapshot = {
        "Order": {
            "type": "object",
            "properties": {
                "by": {"type": "object", "properties": {"id": {"type": "string"}}},
            },
        },
    }

    flatten_inline_schemas(spec)

    assert spec["components"]["schemas"] == snapshot


def test_ref_only_schemas_are_skipped() -> None:
    """Schemas that already are `$ref`s are not touched — only inline shapes get extracted."""
    spec = _empty_spec()
    spec["components"] = {
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {"by": {"$ref": "#/components/schemas/Actor"}},
            },
            "Actor": {"type": "object", "properties": {"id": {"type": "string"}}},
        },
    }

    flat = flatten_inline_schemas(spec)

    assert flat["components"]["schemas"]["Order"]["properties"]["by"] == {
        "$ref": "#/components/schemas/Actor",
    }
    assert set(flat["components"]["schemas"].keys()) == {"Order", "Actor"}


def test_collision_across_distinct_shapes_falls_back_to_hash_suffix() -> None:
    """When title, last-name, and parent-qualified candidates all collide with reserved
    names, the namer falls back to a content-hash suffix to guarantee uniqueness."""
    spec = _empty_spec()
    # Reserve the two natural candidates so the third group must hash-suffix.
    spec["components"] = {
        "schemas": {
            "By": {"type": "string"},
            "AuditBy": {"type": "string"},
            "Audit": {
                "type": "object",
                "properties": {
                    "by": {
                        "type": "object",
                        "properties": {"id": {"type": "integer"}},
                    },
                },
            },
        },
    }

    flat = flatten_inline_schemas(spec)

    new_ref = flat["components"]["schemas"]["Audit"]["properties"]["by"]["$ref"]
    name = new_ref.rsplit("/", 1)[-1]
    assert name not in {"By", "AuditBy"}
    # Hash suffix format: <Base>_<8 hex chars>
    assert "_" in name and len(name.rsplit("_", 1)[-1]) == 8
