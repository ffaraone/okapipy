"""External rules file: a project-local override layer for OpenAPI parsing.

A rules file lets a user supply (or override) `x-okapipy-ns` and
`x-okapipy-paginated` at the document root and `x-okapipy-kind` /
`x-okapipy-paginated` / `x-okapipy-exclude` on path-items or operations
without editing the OpenAPI document itself. Rules-file values take
precedence over values declared inline in the spec.

The file must be local. URLs are not supported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from okapipy.parser.errors import RulesFormatError

ALLOWED_HINTS = {"namespace", "collection", "action", "singleton", "resource"}
EXCLUDABLE_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


class OperationRules(BaseModel):
    """Per-method override entry inside a path's rules block."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy_kind: str | None = Field(default=None, alias="x-okapipy-kind")
    x_okapipy_paginated: bool | None = Field(default=None, alias="x-okapipy-paginated")


class PathRules(BaseModel):
    """Rules entry for a single OpenAPI path."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy_kind: str | None = Field(default=None, alias="x-okapipy-kind")
    x_okapipy_exclude: str | list[str] | None = Field(
        default=None, alias="x-okapipy-exclude"
    )
    x_okapipy_paginated: bool | None = Field(default=None, alias="x-okapipy-paginated")
    get: OperationRules | None = None
    post: OperationRules | None = None
    put: OperationRules | None = None
    patch: OperationRules | None = None
    delete: OperationRules | None = None


class Rules(BaseModel):
    """The full rules document."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    x_okapipy_ns: list[str] = Field(default_factory=list, alias="x-okapipy-ns")
    x_okapipy_paginated: bool | None = Field(default=None, alias="x-okapipy-paginated")
    paths: dict[str, PathRules] = Field(default_factory=dict)


def load_rules(source: str | Path | None) -> Rules:
    """Load a rules file from a local path, returning empty rules when `source` is None.

    The file may be JSON or YAML; the format is auto-detected by attempting JSON first
    and falling back to YAML. URLs are rejected because the rules file is project-local.

    Raises:
        RulesFormatError: When the file cannot be read or parsed, or when an
            `x-okapipy-kind` value is not one of the four legal kinds.
    """
    if source is None:
        return Rules()
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulesFormatError(f"failed to read rules from {source!r}: {exc}") from exc
    raw = _parse_text(text, path)
    try:
        rules = Rules.model_validate(raw)
    except ValidationError as exc:
        raise RulesFormatError(f"invalid rules at {source!r}: {exc}") from exc
    _validate_hints(rules)
    return rules


def path_exclusion(rules: Rules, path: str) -> str | list[str] | None:
    """Return the raw `x-okapipy-exclude` value declared in the rules for `path`.

    The shape mirrors the spec extension: `"*"` for whole-path exclusion, a list of
    HTTP method names for partial exclusion, or `None` when nothing is declared.
    """
    item = rules.paths.get(path)
    return item.x_okapipy_exclude if item is not None else None


def operation_hint(rules: Rules, path: str, method: str) -> str | None:
    """Return the rules' `x-okapipy-kind` for a specific operation, if set.

    The lookup falls back to the path-item-level `x-okapipy-kind` when no per-method
    value exists.
    """
    item = rules.paths.get(path)
    if item is None:
        return None
    op_attr = method.lower()
    op: OperationRules | None = None
    if op_attr in {"get", "post", "put", "patch", "delete"}:
        op = getattr(item, op_attr)
    if op is not None and op.x_okapipy_kind is not None:
        return op.x_okapipy_kind
    return item.x_okapipy_kind


def path_item_hint(rules: Rules, path: str) -> str | None:
    """Return the path-item-level `x-okapipy-kind`, ignoring per-method overrides."""
    item = rules.paths.get(path)
    return item.x_okapipy_kind if item is not None else None


def root_paginated(rules: Rules) -> bool | None:
    """Return the rules' document-root `x-okapipy-paginated`, if set.

    Acts as the default for every operation across the spec when no closer
    override exists; path-item and operation entries still win on conflict.
    """
    return rules.x_okapipy_paginated


def path_item_paginated(rules: Rules, path: str) -> bool | None:
    """Return the rules' path-item-level `x-okapipy-paginated`, if set."""
    item = rules.paths.get(path)
    return item.x_okapipy_paginated if item is not None else None


def operation_paginated(rules: Rules, path: str, method: str) -> bool | None:
    """Return the rules' per-method `x-okapipy-paginated` for `method`, if set.

    Falls back to the path-item-level value when the per-method entry is silent;
    returns `None` when neither is declared.
    """
    item = rules.paths.get(path)
    if item is None:
        return None
    op_attr = method.lower()
    op: OperationRules | None = None
    if op_attr in {"get", "post", "put", "patch", "delete"}:
        op = getattr(item, op_attr)
    if op is not None and op.x_okapipy_paginated is not None:
        return op.x_okapipy_paginated
    return item.x_okapipy_paginated


def extra_namespaces(rules: Rules) -> set[str]:
    """Return the set of namespace paths declared by the rules' `x-okapipy-ns`.

    Entries are normalized by stripping a leading `/` so users can write either
    `accounts` or `/accounts`; the classifier compares against the slash-less
    `cumulative_path` form built by the builder.
    """
    return {item.lstrip("/") for item in rules.x_okapipy_ns}


def _parse_text(text: str, path: Path) -> dict[str, Any]:
    """Parse JSON-or-YAML text and return a dict, normalizing errors."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RulesFormatError(
                f"rules at {path} is not valid JSON or YAML: {exc}"
            ) from exc
    if data is None:
        # An empty file (or one containing only YAML `null`) is a no-op: it
        # carries no rules. Treated as `{}` so consumers can drop a rules
        # file as a placeholder without tripping the loader.
        return {}
    if not isinstance(data, dict):
        raise RulesFormatError(f"rules at {path} must be a mapping at the root")
    return data


def _validate_hints(rules: Rules) -> None:
    """Reject any `x-okapipy-kind` value outside the four legal kinds.

    Also validates `x-okapipy-exclude` entries: each must be either the literal `"*"`
    or a list of HTTP method names (case-insensitive) drawn from the supported set.
    """
    for path, item in rules.paths.items():
        if item.x_okapipy_kind is not None and item.x_okapipy_kind not in ALLOWED_HINTS:
            raise RulesFormatError(
                f"rules path {path!r}: unknown x-okapipy-kind value {item.x_okapipy_kind!r}"
            )
        _validate_exclusion(path, item.x_okapipy_exclude)
        for method in ("get", "post", "put", "patch", "delete"):
            op: OperationRules | None = getattr(item, method)
            if (
                op is not None
                and op.x_okapipy_kind is not None
                and op.x_okapipy_kind not in ALLOWED_HINTS
            ):
                raise RulesFormatError(
                    f"rules path {path!r} method {method!r}: "
                    f"unknown x-okapipy-kind value {op.x_okapipy_kind!r}"
                )


def _validate_exclusion(path: str, value: str | list[str] | None) -> None:
    """Ensure `value` is None, the literal `"*"`, or a list of valid HTTP methods."""
    if value is None or value == "*":
        return
    if not isinstance(value, list):
        raise RulesFormatError(
            f"rules path {path!r}: x-okapipy-exclude must be '*' or a list of "
            f"HTTP methods, got {value!r}"
        )
    for entry in value:
        if not isinstance(entry, str):
            raise RulesFormatError(
                f"rules path {path!r}: x-okapipy-exclude list entry must be a "
                f"string, got {entry!r}"
            )
        if entry.upper() not in EXCLUDABLE_METHODS:
            raise RulesFormatError(
                f"rules path {path!r}: x-okapipy-exclude method {entry!r} is not "
                f"one of {sorted(EXCLUDABLE_METHODS)}"
            )
