"""Per-operation context and Python-type rendering shared by every emitter.

`op_context` translates a parser `Operation` into the small dict shape templates
consume. `collect_model_names` / `filter_model_name` keep generated import
lines honest by dropping schema names dmcg did not actually emit. The
`Shape` literal selects how body and response types are spelled
(model-only, dict-only, or the auto-union both arms).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from okapipy.parser.model import Operation

Shape = Literal["auto", "models", "dicts"]

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def op_context(
    op: Operation | None,
    available_models: set[str] | None = None,
    shape: Shape = "auto",
) -> dict[str, Any] | None:
    """Translate an Operation into the small dict templates need."""
    if op is None:
        return None
    request_model = filter_model_name(op.request_model, available_models)
    members = [
        name
        for name in op.request_model_members
        if available_models is None or name in available_models
    ]
    has_body = bool(op.request_model) or bool(op.request_model_members)
    response_model = filter_model_name(op.response_model, available_models)
    return {
        "method": op.method,
        "response_model": response_model,
        "request_model": request_model,
        "request_model_members": members,
        "body_type": _body_type(request_model, members, shape),
        "response_type": _response_type(response_model, shape),
        "has_body": has_body,
        "pagination_supported": op.pagination_supported,
        "filter_supported": op.filter_supported,
        "sort_supported": op.sort_supported,
    }


def iterator_item_type(item_model: str | None, shape: Shape) -> str:
    """Render the per-item type yielded by a collection iterator.

    Distinct from the response-type renderer: a collection iterator signals
    exhaustion by raising `StopIteration` / `StopAsyncIteration`, never by
    yielding `None`. Each yielded value is either a parsed model instance, a
    raw JSON object, or both depending on `shape`; never `None`. The
    collection's `first()` accessor is the only place where `None` is a
    legitimate return (no items at all); the template adds `| None` there.
    """
    if shape == "dicts":
        return "dict[str, Any]"
    if shape == "models":
        return item_model if item_model else "dict[str, Any]"
    if item_model:
        return f"{item_model} | dict[str, Any]"
    return "dict[str, Any]"


def collect_model_names(
    operations: Sequence[Operation | None],
    available_models: set[str] | None,
) -> set[str]:
    """Return the set of Pydantic model names referenced by `operations`.

    Used to emit `from ..models import <names>` at the top of each generated
    collection / resource / action file. Empty when every operation is `None`
    or has no `request_model` / `response_model`. Names not in `available_models`
    are filtered out so the generated import line never references a symbol
    dmcg didn't actually emit (e.g. schemas inlined as primitive aliases).
    """
    names: set[str] = set()
    for op in operations:
        if op is None:
            continue
        if op.request_model:
            names.add(op.request_model)
        names.update(op.request_model_members)
        if op.response_model:
            names.add(op.response_model)
    if available_models is not None:
        names &= available_models
    return names


def filter_model_name(
    name: str | None, available_models: set[str] | None
) -> str | None:
    """Return `name` if dmcg emitted a matching symbol; otherwise `None`.

    A `None` response_model causes the runtime `from_response` to short-circuit
    and yield raw dicts, which is the right behavior when the schema couldn't
    be modeled as a typed class.

    dmcg strips non-alphanumeric characters from `$ref` schema names — e.g.
    `LimitOffsetPage_OrganizationRead_` becomes `LimitOffsetPageOrganizationRead`.
    The parser, on the other hand, copies the original ref segment verbatim. We
    apply the same normalization as a fallback so generic-style names recovered
    by the parser still resolve to the class dmcg actually emitted.
    """
    if name is None:
        return None
    if available_models is None:
        return name
    if name in available_models:
        return name
    sanitized = dmcg_class_name(name)
    if sanitized in available_models:
        return sanitized
    return None


def dmcg_class_name(name: str) -> str:
    """PascalCase a `$ref` schema name the way `datamodel-code-generator` does.

    Splits on every non-alphanumeric run, drops empty parts, and capitalizes
    the first letter of each surviving fragment while preserving the rest of
    its casing. `LimitOffsetPage_OrganizationRead_` → `LimitOffsetPageOrganizationRead`.
    """
    parts = _NON_ALNUM.split(name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _response_type(response_model: str | None, shape: Shape) -> str:
    """Render the Python return type for an operation that calls `from_response`.

    The result depends on the configured `shape`:

    * `auto` admits both arms — `Foo | dict[str, Any] | None` when a schema
      name was recovered, `dict[str, Any] | None` otherwise.
    * `models` types known schemas as `Foo | None`; unrecovered schemas fall
      back to `dict[str, Any] | None` because the runtime returns raw JSON
      when no class was emitted for the response.
    * `dicts` always types the return as `dict[str, Any] | None` — the client
      never validates, so the model arm is unreachable.
    """
    if shape == "dicts":
        return "dict[str, Any] | None"
    if shape == "models":
        if response_model:
            return f"{response_model} | None"
        return "dict[str, Any] | None"
    if response_model:
        return f"{response_model} | dict[str, Any] | None"
    return "dict[str, Any] | None"


def _body_type(request_model: str | None, members: Sequence[str], shape: Shape) -> str:
    """Render the Python type expression for the operation's `body` parameter.

    The result depends on the configured `shape`:

    * `auto` admits a plain `dict[str, Any]` alongside any typed model(s) so
      callers may pass a raw payload without satisfying the Pydantic class —
      the runtime serializes models or dicts interchangeably. Multiple
      `anyOf` members produce `A | B | dict[str, Any]`; a single class
      produces `A | dict[str, Any]`; an empty/filtered request schema falls
      back to `Any`.
    * `models` drops the dict arm — body must be the recovered model
      (or anyOf union); falls back to `Any` when no schema name was
      recovered.
    * `dicts` types every body as `dict[str, Any]` regardless of any
      recovered schema name (and `Any` when no schema was recovered, to
      keep callers unconstrained).
    """
    if shape == "dicts":
        if request_model or members:
            return "dict[str, Any]"
        return "Any"
    if shape == "models":
        if members:
            return " | ".join(members)
        if request_model:
            return request_model
        return "Any"
    if members:
        return " | ".join([*members, "dict[str, Any]"])
    if request_model:
        return f"{request_model} | dict[str, Any]"
    return "Any"
