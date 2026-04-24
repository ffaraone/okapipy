"""Helpers that read okapipy-specific extensions from a raw OpenAPI document.

The structural parser combines these spec-derived hints with the matching values from
a sidecar; sidecar values win on conflict.
"""

from __future__ import annotations

from typing import Any

OKAPIPY_EXT = "x-okapipy"
OKAPIPY_NS_EXT = "x-okapipy-ns"


def root_namespaces(spec: dict[str, Any]) -> set[str]:
    """Return the set of namespace paths declared by `x-okapipy-ns` at the root."""
    raw = spec.get(OKAPIPY_NS_EXT, [])
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def operation_extension(operation: dict[str, Any]) -> str | None:
    """Return the `x-okapipy` value declared on a single OpenAPI operation, if any."""
    value = operation.get(OKAPIPY_EXT)
    return value if isinstance(value, str) else None


def path_item_extension(path_item: dict[str, Any]) -> str | None:
    """Return the `x-okapipy` value declared at the path-item level, if any."""
    value = path_item.get(OKAPIPY_EXT)
    return value if isinstance(value, str) else None
