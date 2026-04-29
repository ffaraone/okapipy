"""Disambiguation sidecar: external file mirroring the OpenAPI extension shape.

A sidecar lets a user supply (or override) `x-okapipy-ns` at the document root and
`x-okapipy` on path-items or operations without editing the OpenAPI document itself.
Sidecar values take precedence over values declared inline in the spec.

The sidecar must be a local file. URLs are not supported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from okapipy.parser.errors import SidecarFormatError

ALLOWED_HINTS = {"namespace", "collection", "action", "resource"}
EXCLUDABLE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class SidecarOperation(BaseModel):
    """Per-method override entry inside a sidecar path-item."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy: str | None = Field(default=None, alias="x-okapipy")
    x_okapipy_paginated: bool | None = Field(
        default=None, alias="x-okapipy-paginated"
    )


class SidecarPathItem(BaseModel):
    """Sidecar entry for a single OpenAPI path."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy: str | None = Field(default=None, alias="x-okapipy")
    x_okapipy_exclude: str | list[str] | None = Field(
        default=None, alias="x-okapipy-exclude"
    )
    x_okapipy_paginated: bool | None = Field(
        default=None, alias="x-okapipy-paginated"
    )
    get: SidecarOperation | None = None
    post: SidecarOperation | None = None
    put: SidecarOperation | None = None
    patch: SidecarOperation | None = None
    delete: SidecarOperation | None = None


class Sidecar(BaseModel):
    """The full sidecar document."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy_ns: list[str] = Field(default_factory=list, alias="x-okapipy-ns")
    paths: dict[str, SidecarPathItem] = Field(default_factory=dict)


def load_sidecar(source: str | Path | None) -> Sidecar:
    """Load a sidecar from a local path, returning an empty sidecar when `source` is None.

    The file may be JSON or YAML; the format is auto-detected by attempting JSON first
    and falling back to YAML. URLs are rejected because the sidecar is project-local.

    Raises:
        SidecarFormatError: When the file cannot be read or parsed, or when an
            `x-okapipy` value is not one of the four legal kinds.
    """
    if source is None:
        return Sidecar()
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SidecarFormatError(f"failed to read sidecar from {source!r}: {exc}") from exc
    raw = _parse_text(text, path)
    try:
        sidecar = Sidecar.model_validate(raw)
    except ValidationError as exc:
        raise SidecarFormatError(f"invalid sidecar at {source!r}: {exc}") from exc
    _validate_hints(sidecar)
    return sidecar


def path_exclusion(sidecar: Sidecar, path: str) -> str | list[str] | None:
    """Return the raw `x-okapipy-exclude` value declared in the sidecar for `path`.

    The shape mirrors the spec extension: `"*"` for whole-path exclusion, a list of
    HTTP method names for partial exclusion, or `None` when nothing is declared.
    """
    item = sidecar.paths.get(path)
    return item.x_okapipy_exclude if item is not None else None


def operation_hint(sidecar: Sidecar, path: str, method: str) -> str | None:
    """Return the sidecar's `x-okapipy` for a specific operation, if set.

    The lookup falls back to the path-item-level `x-okapipy` when no per-method value
    exists.
    """
    item = sidecar.paths.get(path)
    if item is None:
        return None
    op_attr = method.lower()
    op: SidecarOperation | None = None
    if op_attr in {"get", "post", "put", "patch", "delete"}:
        op = getattr(item, op_attr)
    if op is not None and op.x_okapipy is not None:
        return op.x_okapipy
    return item.x_okapipy


def path_item_hint(sidecar: Sidecar, path: str) -> str | None:
    """Return the path-item-level `x-okapipy`, ignoring per-method overrides."""
    item = sidecar.paths.get(path)
    return item.x_okapipy if item is not None else None


def path_item_paginated(sidecar: Sidecar, path: str) -> bool | None:
    """Return the sidecar's path-item-level `x-okapipy-paginated`, if set."""
    item = sidecar.paths.get(path)
    return item.x_okapipy_paginated if item is not None else None


def operation_paginated(sidecar: Sidecar, path: str, method: str) -> bool | None:
    """Return the sidecar's per-method `x-okapipy-paginated` for `method`, if set.

    Falls back to the path-item-level value when the per-method entry is silent;
    returns `None` when neither is declared.
    """
    item = sidecar.paths.get(path)
    if item is None:
        return None
    op_attr = method.lower()
    op: SidecarOperation | None = None
    if op_attr in {"get", "post", "put", "patch", "delete"}:
        op = getattr(item, op_attr)
    if op is not None and op.x_okapipy_paginated is not None:
        return op.x_okapipy_paginated
    return item.x_okapipy_paginated


def extra_namespaces(sidecar: Sidecar) -> set[str]:
    """Return the set of namespace paths declared by the sidecar's `x-okapipy-ns`."""
    return set(sidecar.x_okapipy_ns)


def _parse_text(text: str, path: Path) -> dict[str, Any]:
    """Parse JSON-or-YAML text and return a dict, normalizing errors."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise SidecarFormatError(f"sidecar at {path} is not valid JSON or YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SidecarFormatError(f"sidecar at {path} must be a mapping at the root")
    return data


def _validate_hints(sidecar: Sidecar) -> None:
    """Reject any `x-okapipy` value outside the four legal kinds.

    Also validates `x-okapipy-exclude` entries: each must be either the literal `"*"`
    or a list of HTTP method names (case-insensitive) drawn from the supported set.
    """
    for path, item in sidecar.paths.items():
        if item.x_okapipy is not None and item.x_okapipy not in ALLOWED_HINTS:
            raise SidecarFormatError(
                f"sidecar path {path!r}: unknown x-okapipy value {item.x_okapipy!r}"
            )
        _validate_exclusion(path, item.x_okapipy_exclude)
        for method in ("get", "post", "put", "patch", "delete"):
            op: SidecarOperation | None = getattr(item, method)
            if op is not None and op.x_okapipy is not None and op.x_okapipy not in ALLOWED_HINTS:
                raise SidecarFormatError(
                    f"sidecar path {path!r} method {method!r}: "
                    f"unknown x-okapipy value {op.x_okapipy!r}"
                )


def _validate_exclusion(path: str, value: str | list[str] | None) -> None:
    """Ensure `value` is None, the literal `"*"`, or a list of valid HTTP methods."""
    if value is None or value == "*":
        return
    if not isinstance(value, list):
        raise SidecarFormatError(
            f"sidecar path {path!r}: x-okapipy-exclude must be '*' or a list of "
            f"HTTP methods, got {value!r}"
        )
    for entry in value:
        if not isinstance(entry, str):
            raise SidecarFormatError(
                f"sidecar path {path!r}: x-okapipy-exclude list entry must be a "
                f"string, got {entry!r}"
            )
        if entry.upper() not in EXCLUDABLE_METHODS:
            raise SidecarFormatError(
                f"sidecar path {path!r}: x-okapipy-exclude method {entry!r} is not "
                f"one of {sorted(EXCLUDABLE_METHODS)}"
            )
