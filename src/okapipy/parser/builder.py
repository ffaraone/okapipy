"""Phase 3 step 2 + Phase 4: walk the resolved spec and build the structural tree.

The builder mutates `APIModel` and its child Pydantic models directly. It owns the
naming engine (PascalCase + breadcrumb-driven contextual names), per-segment node
attachment, and per-method operation routing.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from spacy.language import Language

from okapipy.parser.classifier import SegmentKind, classify_segment
from okapipy.parser.errors import InvalidStructureError
from okapipy.parser.extension import (
    operation_extension,
    operation_paginated_extension,
    path_item_extension,
    path_item_paginated_extension,
    root_namespaces,
)
from okapipy.parser.extension import (
    path_item_exclusion as spec_path_exclusion,
)
from okapipy.parser.loader import detect_base_path, strip_base_path
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Operation,
    Resource,
)
from okapipy.parser.nlp import analyze_segment, lemma_in_context
from okapipy.parser.rules import (
    Rules,
    extra_namespaces,
    operation_hint,
    operation_paginated,
    path_item_hint,
    path_item_paginated,
)
from okapipy.parser.rules import (
    path_exclusion as rules_path_exclusion,
)

log = logging.getLogger(__name__)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

EXCLUDE_ALL = "*"

type ContainerNode = APIModel | Namespace | Collection | Resource | Action


def build(
    spec: dict[str, Any],
    rules: Rules,
    nlp: Language,
    *,
    strip_prefix: str | None = None,
) -> APIModel:
    """Construct an APIModel from an OpenAPI document.

    `$ref` pointers in the spec are left intact: schema names for `request_model` and
    `response_model` are recovered from the trailing segment of each `$ref`, falling
    back to inline schema `title` when no ref is present.

    Args:
        spec: The OpenAPI document, with `$ref`s preserved as in the source.
        rules: A loaded `Rules` document (possibly empty).
        nlp: A loaded spaCy pipeline used by the classifier and naming engine.
        strip_prefix: Optional path prefix to strip from every path before
            classification, e.g. `/public/v1`. When set, this overrides the prefix
            inferred from `servers[].url`.

    Returns:
        A populated APIModel.
    """
    api = APIModel()
    paths_obj = spec.get("paths") or {}
    if not paths_obj:
        return api
    base = strip_prefix if strip_prefix is not None else detect_base_path(spec)
    paths = strip_base_path(paths_obj, base)
    ns_registry = root_namespaces(spec) | extra_namespaces(rules)
    log.debug(
        "builder starting: %d paths, %d namespace hints, base=%r",
        len(paths),
        len(ns_registry),
        base,
    )
    for path, path_item in paths.items():
        exclusion = _resolve_exclusion(path_item, rules, path)
        if exclusion == EXCLUDE_ALL:
            log.info("excluding path %s (x-okapipy-exclude='*')", path)
            continue
        excluded_methods: set[str] = exclusion if isinstance(exclusion, set) else set()
        if _all_operations_deprecated(path_item, excluded_methods):
            log.info("skipping path %s (all operations deprecated)", path)
            continue
        try:
            _walk_path(
                api=api,
                path=path,
                path_item=path_item,
                rules=rules,
                nlp=nlp,
                ns_registry=ns_registry,
                excluded_methods=excluded_methods,
            )
        except InvalidStructureError as exc:
            log.warning("skipping path %s: %s", path, exc)
    return api


def _all_operations_deprecated(
    path_item: dict[str, Any], excluded_methods: set[str]
) -> bool:
    """Return True when every non-excluded operation on `path_item` is `deprecated`.

    Used to skip an entire path before walking it: if the path has no live
    operations left after deprecation + exclusion filtering, building its
    structural tree is wasted work and may produce confusing partial trees
    (a Collection whose only fetch op was deprecated, etc.).
    """
    has_live = False
    for method in HTTP_METHODS:
        op = path_item.get(method)
        if not isinstance(op, dict):
            continue
        if method.upper() in excluded_methods:
            continue
        if op.get("deprecated") is True:
            continue
        has_live = True
        break
    return not has_live


def _resolve_exclusion(
    path_item: dict[str, Any],
    rules: Rules,
    path: str,
) -> str | set[str]:
    """Return the merged exclusion for a path: `'*'`, a set of upper methods, or empty.

    Rules wins over spec when both declare an exclusion for the same path.
    """
    chosen = rules_path_exclusion(rules, path)
    if chosen is None:
        chosen = spec_path_exclusion(path_item)
    if chosen is None:
        return set()
    if chosen == EXCLUDE_ALL:
        return EXCLUDE_ALL
    if isinstance(chosen, list):
        return {method.upper() for method in chosen if isinstance(method, str)}
    return set()


def contextual_name(breadcrumb: list[str], current: str) -> str:
    """Return a contextual PascalCase name built from the full breadcrumb chain.

    Every singular collection name accumulated in `breadcrumb` is concatenated, then
    the PascalCase form of `current` is appended. With an empty breadcrumb, only
    `PascalCase(current)` is returned.

    Examples:
        contextual_name([], "orders") == "Orders"
        contextual_name(["Order"], "lines") == "OrderLines"
        contextual_name(["Organization", "Datasource"], "force-reimport")
            == "OrganizationDatasourceForceReimport"
    """
    rendered = _pascal_case(current)
    if not breadcrumb:
        return rendered
    return "".join(breadcrumb) + rendered


def singularize(token: str, nlp: Language) -> str:
    """Return `token` reduced to singular form when its head word is plural.

    Compound segments like `recovery-requests` are handled by lemmatizing only the
    head (the last sub-word), since in English the head carries the plural marking.
    The separator between sub-words is preserved, so `password-recovery-requests`
    becomes `password-recovery-request`.

    The result is PascalCase-friendly: callers that want a class-style name still need
    to run it through `_pascal_case`.
    """
    parts = re.split(r"([-_])", token)
    words = [parts[i] for i in range(0, len(parts), 2)]
    if not any(words):
        return token
    head = words[-1]
    info = analyze_segment(nlp, head)
    if not info.is_plural:
        return token
    words[-1] = lemma_in_context(nlp, head)
    rebuilt: list[str] = []
    for index, word in enumerate(words):
        rebuilt.append(word)
        sep_index = index * 2 + 1
        if sep_index < len(parts):
            rebuilt.append(parts[sep_index])
    return "".join(rebuilt)


def _walk_path(
    *,
    api: APIModel,
    path: str,
    path_item: dict[str, Any],
    rules: Rules,
    nlp: Language,
    ns_registry: set[str],
    excluded_methods: set[str],
) -> None:
    """Walk a single OpenAPI path and attach its operations to the tree."""
    log.debug("walking path %s", path)
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return
    cursor: ContainerNode = api
    breadcrumb: list[str] = []
    parent_kind: SegmentKind | None = None
    cumulative_parts: list[str] = []
    # The full-path hint (rules wins over spec extension) applies to the last
    # segment; intermediate segments resolve their hints by cumulative-path
    # lookup so a rules entry like `/helpdesk/feedback: collection` propagates
    # to every nested path that walks through it.
    full_path_hint = _merge_hint(
        path_item_hint(rules, path),
        path_item_extension(path_item),
    )
    terminal_kind: SegmentKind | None = None
    last_path = ""
    for index, segment in enumerate(segments):
        cumulative_parts.append(segment)
        cumulative_path = "/".join(cumulative_parts)
        is_last = index == len(segments) - 1
        if is_last:
            hint = full_path_hint
        else:
            hint = path_item_hint(rules, "/" + cumulative_path)
        kind = classify_segment(
            segment=segment,
            cumulative_path=cumulative_path,
            parent_kind=parent_kind,
            nlp=nlp,
            ns_registry=ns_registry,
            extension_hint=hint,
        )
        cursor, breadcrumb = _attach(
            cursor=cursor,
            kind=kind,
            segment=segment,
            cumulative_path="/" + cumulative_path,
            breadcrumb=breadcrumb,
            nlp=nlp,
        )
        terminal_kind = kind
        parent_kind = kind
        last_path = "/" + cumulative_path
    if terminal_kind is None:
        return
    _install_operations(
        cursor=cursor,
        terminal_kind=terminal_kind,
        path_item=path_item,
        rules=rules,
        path=path,
        action_path=last_path,
        excluded_methods=excluded_methods,
    )


def _attach(
    *,
    cursor: ContainerNode,
    kind: SegmentKind,
    segment: str,
    cumulative_path: str,
    breadcrumb: list[str],
    nlp: Language,
) -> tuple[ContainerNode, list[str]]:
    """Find or create the child node for `segment` under `cursor` and return both."""
    if kind is SegmentKind.NAMESPACE:
        if not isinstance(cursor, (APIModel, Namespace)):
            raise InvalidStructureError(
                f"namespace segment {segment!r} cannot be attached under {type(cursor).__name__}"
            )
        existing = next((n for n in cursor.namespaces if n.name == segment), None)
        if existing is None:
            existing = Namespace(name=segment)
            cursor.namespaces.append(existing)
        return existing, breadcrumb
    if kind is SegmentKind.COLLECTION:
        if not isinstance(cursor, (APIModel, Namespace, Resource)):
            raise InvalidStructureError(
                f"collection segment {segment!r} cannot be attached under {type(cursor).__name__}"
            )
        name = contextual_name(breadcrumb, segment)
        collections_list = cursor.collections
        existing_col = next((c for c in collections_list if c.name == name), None)
        if existing_col is None:
            existing_col = Collection(name=name, path=cumulative_path)
            collections_list.append(existing_col)
        new_breadcrumb = breadcrumb + [_pascal_case(singularize(segment, nlp))]
        return existing_col, new_breadcrumb
    if kind is SegmentKind.RESOURCE_ID:
        if not isinstance(cursor, Collection):
            raise InvalidStructureError(
                f"resource id {segment!r} must follow a collection, found {type(cursor).__name__}"
            )
        if cursor.resource is None:
            resource_name = (
                "".join(breadcrumb) if breadcrumb else _pascal_case(cursor.name)
            )
            cursor.resource = Resource(name=resource_name, path=cumulative_path)
        return cursor.resource, breadcrumb
    if kind is SegmentKind.ACTION:
        if isinstance(cursor, (APIModel, Namespace)):
            raise InvalidStructureError(
                f"action {segment!r} cannot live directly under a namespace at {cumulative_path!r}"
            )
        if not isinstance(cursor, (Collection, Resource)):
            raise InvalidStructureError(
                f"action {segment!r} cannot be attached under {type(cursor).__name__}"
            )
        name = contextual_name(breadcrumb, segment)
        existing_act = next((a for a in cursor.actions if a.name == name), None)
        if existing_act is None:
            existing_act = Action(name=name, path=cumulative_path)
            cursor.actions.append(existing_act)
        return existing_act, breadcrumb
    raise InvalidStructureError(f"unhandled segment kind {kind!r}")


def _install_operations(
    *,
    cursor: ContainerNode,
    terminal_kind: SegmentKind,
    path_item: dict[str, Any],
    rules: Rules,
    path: str,
    action_path: str,
    excluded_methods: set[str],
) -> None:
    """Attach Operation entries onto the terminal node according to its kind."""
    item_paginated = _resolve_paginated(
        rules_value=path_item_paginated(rules, path),
        spec_value=path_item_paginated_extension(path_item),
    )
    for method in HTTP_METHODS:
        op_data = path_item.get(method)
        if not isinstance(op_data, dict):
            continue
        if method.upper() in excluded_methods:
            log.info("excluding %s %s (x-okapipy-exclude)", method.upper(), action_path)
            continue
        if op_data.get("deprecated") is True:
            log.info("skipping %s %s (deprecated)", method.upper(), action_path)
            continue
        method_hint = _merge_hint(
            operation_hint(rules, path, method),
            operation_extension(op_data),
        )
        is_action_method = method_hint == SegmentKind.ACTION.value
        op_paginated = _resolve_paginated(
            rules_value=operation_paginated(rules, path, method),
            spec_value=operation_paginated_extension(op_data),
            fallback=item_paginated,
        )
        operation = _build_operation(
            method=method,
            op_data=op_data,
            pagination_supported=op_paginated,
        )
        _route(
            cursor=cursor,
            terminal_kind=terminal_kind,
            method=method,
            operation=operation,
            action_path=action_path,
            is_action_method=is_action_method,
        )


def _resolve_paginated(
    *,
    rules_value: bool | None,
    spec_value: bool | None,
    fallback: bool = True,
) -> bool:
    """Merge a paginated flag with rules precedence; fall back to `fallback` if unset."""
    if rules_value is not None:
        return rules_value
    if spec_value is not None:
        return spec_value
    return fallback


def _route(
    *,
    cursor: ContainerNode,
    terminal_kind: SegmentKind,
    method: str,
    operation: Operation,
    action_path: str,
    is_action_method: bool,
) -> None:
    """Place a single Operation on the terminal node based on its kind and method."""
    if isinstance(cursor, (APIModel, Namespace)):
        raise InvalidStructureError(
            f"cannot attach {method.upper()} to namespace at {action_path!r}; "
            "namespace-level actions are not allowed"
        )
    if terminal_kind is SegmentKind.ACTION and isinstance(cursor, Action):
        cursor.operations.append(operation)
        return
    if isinstance(cursor, Collection):
        if is_action_method:
            _attach_synthetic_action(cursor, action_path, operation)
            return
        if method == "get":
            cursor.fetch = operation
        elif method == "post":
            cursor.create = operation
        else:
            log.warning(
                "skipping %s %s: method has no canonical slot on collection %r and "
                "the operation does not fit the namespace/collection/resource/action "
                "hierarchy. Mark it with x-okapipy-kind: action to keep it.",
                method.upper(),
                action_path,
                cursor.name,
            )
        return
    if isinstance(cursor, Resource):
        if is_action_method:
            _attach_synthetic_action(cursor, action_path, operation)
            return
        if method == "get":
            cursor.retrieve = operation
        elif method == "put":
            cursor.update = operation
        elif method == "patch":
            cursor.partial_update = operation
        elif method == "delete":
            cursor.delete = operation
        else:
            log.warning(
                "skipping %s %s: method has no canonical slot on resource %r and "
                "the operation does not fit the namespace/collection/resource/action "
                "hierarchy. Mark it with x-okapipy-kind: action to keep it.",
                method.upper(),
                action_path,
                cursor.name,
            )


def _attach_synthetic_action(
    parent: Collection | Resource,
    path: str,
    operation: Operation,
) -> None:
    """Append `operation` to a synthesized Action under `parent`.

    The action is named after the path's last segment when that segment is a
    descriptive word (e.g. `submit` → `Submit`). When the last segment is a path
    parameter such as `{id}` — meaning the operation hits the resource itself with
    no further sub-path — the name falls back to `<ParentName><Method>` (e.g.
    `PasswordRecoveryRequestPost`), since reusing the parameter token would yield
    nonsense like `{email}` as an action name.
    """
    last = path.rstrip("/").rsplit("/", 1)[-1] or "action"
    if last.startswith("{") and last.endswith("}"):
        name = f"{parent.name}{operation.method.capitalize()}"
    else:
        name = _pascal_case(last)
    existing = next((a for a in parent.actions if a.name == name), None)
    if existing is None:
        existing = Action(name=name, path=path)
        parent.actions.append(existing)
    existing.operations.append(operation)


def _build_operation(
    *,
    method: str,
    op_data: dict[str, Any],
    pagination_supported: bool,
) -> Operation:
    """Build an Operation from one method entry, reading schema names from `$ref`s."""
    summary = op_data.get("summary")
    description = op_data.get("description")
    request_content_type, request_model = _request_info(op_data)
    response_content_type, response_model, item_model, response_headers = (
        _response_info(op_data)
    )
    return Operation(
        method=method.upper(),
        summary=summary if isinstance(summary, str) else None,
        description=description if isinstance(description, str) else None,
        request_content_type=request_content_type,
        request_model=request_model,
        response_content_type=response_content_type,
        response_model=response_model,
        item_model=item_model,
        response_headers=response_headers,
        pagination_supported=pagination_supported,
    )


def _request_info(op_data: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return `(content_type, schema_name)` for the operation's request body, if any."""
    body = op_data.get("requestBody")
    if not isinstance(body, dict):
        return None, None
    content = body.get("content")
    if not isinstance(content, dict) or not content:
        return None, None
    content_type = next(iter(content))
    entry = content.get(content_type)
    schema = entry.get("schema") if isinstance(entry, dict) else None
    return content_type, _name_from_schema(schema)


def _response_info(
    op_data: dict[str, Any],
) -> tuple[str | None, str | None, str | None, list[str]]:
    """Return `(content_type, schema_name, item_name, header_names)` for the chosen 2xx response.

    `schema_name` always names the literal response body schema as declared (the
    envelope, when one wraps a list). `item_name` names the inner element schema
    when the response is list-shaped — either a plain `type: array` or an object
    with a known data-array property (`items`, `data`, `results`, `records`,
    `entries`); `None` otherwise. The generator uses `item_name` so paginated
    iteration yields typed model instances.
    """
    responses = op_data.get("responses")
    if not isinstance(responses, dict):
        return None, None, None, []
    status = _pick_success_status(responses)
    if status is None:
        return None, None, None, []
    response = responses[status]
    if not isinstance(response, dict):
        return None, None, None, []
    headers = _response_header_names(response)
    content = response.get("content")
    if not isinstance(content, dict) or not content:
        return None, None, None, headers
    content_type = next(iter(content))
    entry = content.get(content_type)
    schema = entry.get("schema") if isinstance(entry, dict) else None
    return (
        content_type,
        _name_from_schema(schema),
        _item_name_from_schema(schema),
        headers,
    )


_ENVELOPE_DATA_KEYS = ("items", "data", "results", "records", "entries")


def _item_name_from_schema(schema: Any) -> str | None:
    """Return the inner item schema name for a list-shaped response, or `None`.

    Recognised shapes: plain `type: array` (item is `schema.items`) and object
    schemas with one of the conventional data-array properties (`items`, `data`,
    `results`, `records`, `entries`). Anything else returns `None` and the
    generator falls back to yielding raw dicts.
    """
    if not isinstance(schema, dict):
        return None
    if schema.get("type") == "array":
        return _name_from_schema(schema.get("items"))
    props = schema.get("properties")
    if not isinstance(props, dict):
        return None
    for key in _ENVELOPE_DATA_KEYS:
        entry = props.get(key)
        if isinstance(entry, dict) and entry.get("type") == "array":
            return _name_from_schema(entry.get("items"))
    return None


def _name_from_schema(schema: Any) -> str | None:
    """Return a schema name, preferring the `$ref`'s trailing segment over `title`."""
    if not isinstance(schema, dict):
        return None
    name = _schema_name(schema)
    if name is not None:
        return name
    title = schema.get("title")
    return title if isinstance(title, str) else None


def _response_header_names(response: dict[str, Any]) -> list[str]:
    """Return the names of headers declared on a 2xx response, preserving order."""
    headers = response.get("headers")
    if not isinstance(headers, dict):
        return []
    return [name for name in headers if isinstance(name, str)]


def _pick_success_status(responses: dict[str, Any]) -> str | None:
    """Pick the most specific 2xx status: 200 > 201 > any other 2xx > None."""
    if "200" in responses:
        return "200"
    if "201" in responses:
        return "201"
    for status in responses:
        if isinstance(status, str) and status.startswith("2") and len(status) == 3:
            return status
    return None


def _schema_name(schema: dict[str, Any]) -> str | None:
    """Return the trailing identifier of a `$ref` like `#/components/schemas/Order`."""
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return None
    return ref.rsplit("/", 1)[-1] or None


def _merge_hint(rules_hint: str | None, spec_hint: str | None) -> str | None:
    """Return the rules hint when set, otherwise the spec hint, otherwise None."""
    if rules_hint is not None:
        return rules_hint
    return spec_hint


_PASCAL_SPLIT = re.compile(r"[-_\s]+")


def _pascal_case(token: str) -> str:
    """Convert a kebab-, snake-, or space-cased token to PascalCase."""
    parts = [p for p in _PASCAL_SPLIT.split(token) if p]
    return "".join(part[:1].upper() + part[1:] for part in parts) or token
