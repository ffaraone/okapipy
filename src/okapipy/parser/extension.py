"""Helpers that read okapipy-specific extensions from a raw OpenAPI document.

The structural parser combines these spec-derived hints with the matching values from
a rules file; rules-file values win on conflict.
"""

from __future__ import annotations

from typing import Any

OKAPIPY_KIND_EXT = "x-okapipy-kind"
OKAPIPY_NS_EXT = "x-okapipy-ns"
OKAPIPY_EXCLUDE_EXT = "x-okapipy-exclude"
OKAPIPY_PAGINATED_EXT = "x-okapipy-paginated"


def root_namespaces(spec: dict[str, Any]) -> set[str]:
    """Return the set of namespace paths declared by `x-okapipy-ns` at the root.

    Entries are normalized by stripping a leading `/` so that both `accounts`
    and `/accounts` register the same path — the classifier compares against
    the slash-less `cumulative_path` form, but users naturally write paths
    with the leading slash.
    """
    raw = spec.get(OKAPIPY_NS_EXT, [])
    if not isinstance(raw, list):
        return set()
    return {item.lstrip("/") for item in raw if isinstance(item, str)}


def operation_extension(operation: dict[str, Any]) -> str | None:
    """Return the `x-okapipy-kind` value declared on a single OpenAPI operation, if any."""
    value = operation.get(OKAPIPY_KIND_EXT)
    return value if isinstance(value, str) else None


def path_item_extension(path_item: dict[str, Any]) -> str | None:
    """Return the `x-okapipy-kind` value declared at the path-item level, if any."""
    value = path_item.get(OKAPIPY_KIND_EXT)
    return value if isinstance(value, str) else None


def root_paginated_extension(spec: dict[str, Any]) -> bool | None:
    """Return the document-root `x-okapipy-paginated` value, if explicitly set.

    Acts as the default for every operation in the document: when set to
    `False`, list endpoints are non-paginated unless a path-item or
    operation re-enables pagination. Returns `None` when the extension is
    absent or has an unsupported shape.
    """
    value = spec.get(OKAPIPY_PAGINATED_EXT)
    return value if isinstance(value, bool) else None


def operation_paginated_extension(operation: dict[str, Any]) -> bool | None:
    """Return the `x-okapipy-paginated` value declared on a single operation.

    Returns `True`/`False` when explicitly set, or `None` when the extension is
    absent or has an unsupported shape.
    """
    value = operation.get(OKAPIPY_PAGINATED_EXT)
    return value if isinstance(value, bool) else None


def path_item_paginated_extension(path_item: dict[str, Any]) -> bool | None:
    """Return the `x-okapipy-paginated` value declared at the path-item level.

    Returns `True`/`False` when explicitly set, or `None` when the extension is
    absent or has an unsupported shape.
    """
    value = path_item.get(OKAPIPY_PAGINATED_EXT)
    return value if isinstance(value, bool) else None


def path_item_exclusion(path_item: dict[str, Any]) -> str | list[str] | None:
    """Return the raw `x-okapipy-exclude` value declared at the path-item level.

    The return type is one of:
        - `"*"` to drop every method on this path,
        - a list of HTTP method names (case unspecified) for partial exclusion,
        - `None` when the extension is absent or has an unsupported shape.
    """
    value = path_item.get(OKAPIPY_EXCLUDE_EXT)
    if value == "*":
        return "*"
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return None
