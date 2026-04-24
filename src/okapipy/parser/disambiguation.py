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


class SidecarOperation(BaseModel):
    """Per-method override entry inside a sidecar path-item."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy: str | None = Field(default=None, alias="x-okapipy")


class SidecarPathItem(BaseModel):
    """Sidecar entry for a single OpenAPI path."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy: str | None = Field(default=None, alias="x-okapipy")
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
    """Reject any `x-okapipy` value outside the four legal kinds."""
    for path, item in sidecar.paths.items():
        if item.x_okapipy is not None and item.x_okapipy not in ALLOWED_HINTS:
            raise SidecarFormatError(
                f"sidecar path {path!r}: unknown x-okapipy value {item.x_okapipy!r}"
            )
        for method in ("get", "post", "put", "patch", "delete"):
            op: SidecarOperation | None = getattr(item, method)
            if op is not None and op.x_okapipy is not None and op.x_okapipy not in ALLOWED_HINTS:
                raise SidecarFormatError(
                    f"sidecar path {path!r} method {method!r}: "
                    f"unknown x-okapipy value {op.x_okapipy!r}"
                )
