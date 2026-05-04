"""Hoist inline schemas in an OpenAPI document into `components.schemas`.

dmcg emits one class per anonymous schema occurrence, so a spec where the same
shape is repeated inline (`Created.by`, `Updated.by`, `Deleted.by`, …) yields
chains like `By` / `By1` / `By2`. This pass walks the spec, lifts every inline
`type: object`-with-properties or inline-`enum` schema into `components.schemas`,
and replaces the inline occurrence with a `$ref`. Structurally identical schemas
collapse to a single component so the duplicates disappear before dmcg runs.

Naming priority for each extracted schema:
1. its `title` (PascalCased)
2. the last property name on the path to the schema
3. parent property + last (e.g. `CreatedBy` instead of `By`)
4. content-hash suffix as a last resort

Existing top-level component names are reserved up front, so a new extraction
never overwrites a component the user named explicitly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


@dataclass
class _Occurrence:
    """One inline-schema slot — its parent container, the key/index, and the breadcrumb."""

    parent: dict[str, Any] | list[Any]
    key: str | int
    schema: dict[str, Any]
    breadcrumb: tuple[str, ...]


def flatten_inline_schemas(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `spec` with inline object/enum schemas hoisted into components.

    The input is not mutated. Schemas already living under `components.schemas` are
    left in place; only inline occurrences (parameters, request bodies, responses,
    nested `properties`, `items`, `additionalProperties`, composition members) are
    extracted. Structurally identical inline schemas collapse to a single component.
    """
    spec = copy.deepcopy(spec)
    components = spec.setdefault("components", {})
    if not isinstance(components, dict):
        return spec
    schemas = components.setdefault("schemas", {})
    if not isinstance(schemas, dict):
        return spec

    occurrences = list(_collect(spec))
    if not occurrences:
        return spec

    by_hash: dict[str, list[_Occurrence]] = {}
    for occ in occurrences:
        by_hash.setdefault(_structural_hash(occ.schema), []).append(occ)

    used_names = set(schemas.keys())
    hash_to_name: dict[str, str] = {}
    for digest, group in by_hash.items():
        name = _choose_name(group, digest, used_names)
        used_names.add(name)
        hash_to_name[digest] = name

    for digest, group in by_hash.items():
        name = hash_to_name[digest]
        schemas[name] = group[0].schema
        ref = {"$ref": f"#/components/schemas/{name}"}
        for occ in group:
            occ.parent[occ.key] = ref  # type: ignore[index]

    return spec


def _collect(spec: dict[str, Any]) -> Iterator[_Occurrence]:
    """Walk every schema-bearing location in `spec` and yield inline extractables."""
    components = spec.get("components") or {}
    schemas = components.get("schemas") if isinstance(components, dict) else None
    if isinstance(schemas, dict):
        for cname, schema in schemas.items():
            if isinstance(schema, dict):
                yield from _walk_schema(schema, breadcrumb=(cname,))

    paths = spec.get("paths") or {}
    if isinstance(paths, dict):
        for item in paths.values():
            if not isinstance(item, dict):
                continue
            yield from _walk_parameters(item.get("parameters"))
            for method in _HTTP_METHODS:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                yield from _walk_parameters(op.get("parameters"))
                rb = op.get("requestBody")
                if isinstance(rb, dict):
                    yield from _walk_body(rb)
                responses = op.get("responses")
                if isinstance(responses, dict):
                    for resp in responses.values():
                        if isinstance(resp, dict):
                            yield from _walk_response(resp)

    if isinstance(components, dict):
        for cat, walker in (
            ("parameters", _walk_param),
            ("requestBodies", _walk_body),
            ("responses", _walk_response),
            ("headers", _walk_param),
        ):
            section = components.get(cat)
            if isinstance(section, dict):
                for value in section.values():
                    if isinstance(value, dict):
                        yield from walker(value)


def _walk_parameters(parameters: Any) -> Iterator[_Occurrence]:
    if not isinstance(parameters, list):
        return
    for param in parameters:
        if isinstance(param, dict):
            yield from _walk_param(param)


def _walk_param(param: dict[str, Any]) -> Iterator[_Occurrence]:
    schema = param.get("schema")
    if isinstance(schema, dict):
        breadcrumb = (param["name"],) if isinstance(param.get("name"), str) else ()
        yield from _maybe_extract(param, "schema", schema, breadcrumb)


def _walk_body(rb: dict[str, Any]) -> Iterator[_Occurrence]:
    content = rb.get("content")
    if not isinstance(content, dict):
        return
    for media in content.values():
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        if isinstance(schema, dict):
            yield from _maybe_extract(media, "schema", schema, breadcrumb=())


def _walk_response(resp: dict[str, Any]) -> Iterator[_Occurrence]:
    yield from _walk_body(resp)
    headers = resp.get("headers")
    if isinstance(headers, dict):
        for header in headers.values():
            if isinstance(header, dict):
                yield from _walk_param(header)


def _maybe_extract(
    parent: dict[str, Any] | list[Any],
    key: str | int,
    schema: dict[str, Any],
    breadcrumb: tuple[str, ...],
) -> Iterator[_Occurrence]:
    """Yield this schema for extraction if eligible, then descend into its sub-schemas."""
    if "$ref" in schema:
        return
    if _is_extractable(schema):
        yield _Occurrence(parent=parent, key=key, schema=schema, breadcrumb=breadcrumb)
    yield from _walk_schema(schema, breadcrumb)


def _walk_schema(
    schema: dict[str, Any],
    breadcrumb: tuple[str, ...],
) -> Iterator[_Occurrence]:
    """Recurse through a schema's nested schemas (properties, items, compositions)."""
    if "$ref" in schema:
        return
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for pname, pschema in properties.items():
            if isinstance(pschema, dict):
                yield from _maybe_extract(
                    properties, pname, pschema, breadcrumb + (pname,)
                )
    items = schema.get("items")
    if isinstance(items, dict):
        yield from _maybe_extract(schema, "items", items, breadcrumb)
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        yield from _maybe_extract(
            schema, "additionalProperties", additional, breadcrumb
        )
    for comp_key in ("allOf", "oneOf", "anyOf"):
        members = schema.get(comp_key)
        if isinstance(members, list):
            for index, member in enumerate(members):
                if isinstance(member, dict):
                    yield from _maybe_extract(members, index, member, breadcrumb)
    not_schema = schema.get("not")
    if isinstance(not_schema, dict):
        yield from _maybe_extract(schema, "not", not_schema, breadcrumb)


def _is_extractable(schema: dict[str, Any]) -> bool:
    """A schema is worth extracting if dmcg would emit a class for it.

    That covers inline objects with declared properties and inline enums; primitives,
    plain arrays, and empty schemas (`{}`, free-form objects) are left alone.
    """
    if "$ref" in schema:
        return False
    if "enum" in schema:
        return True
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        return True
    return False


def _structural_hash(schema: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON of the schema. Used to dedupe identical shapes."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _choose_name(group: list[_Occurrence], digest: str, used: set[str]) -> str:
    """Pick the best non-colliding name for `group`, falling back to a hash suffix."""
    candidates: list[str] = []
    for occ in group:
        title = occ.schema.get("title")
        if isinstance(title, str):
            pascal = _pascal(title)
            if pascal:
                candidates.append(pascal)
                break
    for occ in group:
        if occ.breadcrumb:
            pascal = _pascal(occ.breadcrumb[-1])
            if pascal:
                candidates.append(pascal)
                break
    for occ in group:
        if len(occ.breadcrumb) >= 2:
            qualified = _pascal(occ.breadcrumb[-2]) + _pascal(occ.breadcrumb[-1])
            if qualified:
                candidates.append(qualified)
                break

    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)

    for c in unique:
        if c not in used:
            return c
    base = unique[0] if unique else "Anon"
    return f"{base}_{digest[:8]}"


def _pascal(s: str) -> str:
    """Convert any identifier-like string to PascalCase, dropping non-alphanumerics."""
    parts = re.split(r"[^a-zA-Z0-9]+", s)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)
