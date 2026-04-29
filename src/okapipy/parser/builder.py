"""Phase 3 step 2 + Phase 4: walk the resolved spec and build the structural tree.

The builder mutates `APIModel` and its child Pydantic models directly. It owns the
naming engine (PascalCase + breadcrumb-driven contextual names), per-segment node
attachment, and per-method operation routing.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from spacy.language import Language

from okapipy.parser.classifier import SegmentKind, classify_segment
from okapipy.parser.disambiguation import (
    Sidecar,
    extra_namespaces,
    operation_hint,
    path_item_hint,
)
from okapipy.parser.errors import InvalidStructureError
from okapipy.parser.extension import (
    operation_extension,
    path_item_extension,
    root_namespaces,
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

log = logging.getLogger(__name__)

type ListResponseResolver = Callable[[dict[str, Any]], dict[str, Any]]

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

type ContainerNode = APIModel | Namespace | Collection | Resource | Action


def build(
    spec: dict[str, Any],
    raw_spec: dict[str, Any],
    sidecar: Sidecar,
    nlp: Language,
    list_response_resolver: ListResponseResolver | None = None,
) -> APIModel:
    """Construct an APIModel from a resolved OpenAPI document.

    Args:
        spec: The fully-resolved OpenAPI document (refs inlined).
        raw_spec: The same document **without** ref resolution; used to recover the
            original schema names for `request_model` / `response_model`.
        sidecar: A loaded disambiguation sidecar (possibly empty).
        nlp: A loaded spaCy pipeline used by the classifier and naming engine.
        list_response_resolver: Optional callback that picks the item-schema out of a
            collection list response.

    Returns:
        A populated APIModel.
    """
    api = APIModel()
    paths_obj = spec.get("paths") or {}
    if not paths_obj:
        return api
    base = detect_base_path(spec)
    paths = strip_base_path(paths_obj, base)
    raw_paths = strip_base_path(raw_spec.get("paths") or {}, base)
    ns_registry = root_namespaces(spec) | extra_namespaces(sidecar)
    for path, path_item in paths.items():
        raw_item = raw_paths.get(path, {})
        _walk_path(
            api=api,
            path=path,
            path_item=path_item,
            raw_path_item=raw_item,
            sidecar=sidecar,
            nlp=nlp,
            ns_registry=ns_registry,
            list_response_resolver=list_response_resolver,
        )
    return api


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


def default_list_response_resolver(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the inner item-schema of a list-style response, or the input unchanged.

    The heuristic looks for exactly one array property in `schema.properties`. When
    found, the array's `items` schema is returned; otherwise the original schema is
    returned untouched.
    """
    if not isinstance(schema, dict):
        return schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    arrays = [
        value
        for value in properties.values()
        if isinstance(value, dict) and value.get("type") == "array" and "items" in value
    ]
    if len(arrays) != 1:
        return schema
    items = arrays[0].get("items")
    return items if isinstance(items, dict) else schema


def _walk_path(
    *,
    api: APIModel,
    path: str,
    path_item: dict[str, Any],
    raw_path_item: dict[str, Any],
    sidecar: Sidecar,
    nlp: Language,
    ns_registry: set[str],
    list_response_resolver: ListResponseResolver | None,
) -> None:
    """Walk a single OpenAPI path and attach its operations to the tree."""
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return
    cursor: ContainerNode = api
    breadcrumb: list[str] = []
    parent_kind: SegmentKind | None = None
    cumulative_parts: list[str] = []
    item_hint = _merge_hint(
        path_item_hint(sidecar, path),
        path_item_extension(path_item),
    )
    terminal_kind: SegmentKind | None = None
    last_path = ""
    for index, segment in enumerate(segments):
        cumulative_parts.append(segment)
        cumulative_path = "/".join(cumulative_parts)
        is_last = index == len(segments) - 1
        hint = item_hint if is_last else None
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
        raw_path_item=raw_path_item,
        sidecar=sidecar,
        path=path,
        action_path=last_path,
        list_response_resolver=list_response_resolver,
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
            resource_name = "".join(breadcrumb) if breadcrumb else _pascal_case(cursor.name)
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
    raw_path_item: dict[str, Any],
    sidecar: Sidecar,
    path: str,
    action_path: str,
    list_response_resolver: ListResponseResolver | None,
) -> None:
    """Attach Operation entries onto the terminal node according to its kind."""
    for method in HTTP_METHODS:
        op_data = path_item.get(method)
        if not isinstance(op_data, dict):
            continue
        raw_op = raw_path_item.get(method) if isinstance(raw_path_item, dict) else None
        method_hint = _merge_hint(
            operation_hint(sidecar, path, method),
            operation_extension(op_data),
        )
        is_action_method = method_hint == SegmentKind.ACTION.value
        operation = _build_operation(
            method=method,
            op_data=op_data,
            raw_op=raw_op if isinstance(raw_op, dict) else {},
            apply_list_resolver=(
                terminal_kind is SegmentKind.COLLECTION and method == "get"
            ),
            list_response_resolver=list_response_resolver,
        )
        _route(
            cursor=cursor,
            terminal_kind=terminal_kind,
            method=method,
            operation=operation,
            action_path=action_path,
            is_action_method=is_action_method,
        )


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
                "hierarchy. Mark it with x-okapipy: action to keep it.",
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
                "hierarchy. Mark it with x-okapipy: action to keep it.",
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
    raw_op: dict[str, Any],
    apply_list_resolver: bool,
    list_response_resolver: ListResponseResolver | None,
) -> Operation:
    """Build an Operation from one method entry, recovering ref names from `raw_op`."""
    summary = op_data.get("summary")
    description = op_data.get("description")
    request_content_type, request_model = _request_info(op_data, raw_op)
    response_content_type, response_model = _response_info(
        op_data,
        raw_op,
        apply_list_resolver=apply_list_resolver,
        list_response_resolver=list_response_resolver,
    )
    return Operation(
        method=method.upper(),
        summary=summary if isinstance(summary, str) else None,
        description=description if isinstance(description, str) else None,
        request_content_type=request_content_type,
        request_model=request_model,
        response_content_type=response_content_type,
        response_model=response_model,
    )


def _request_info(
    op_data: dict[str, Any], raw_op: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Return `(content_type, schema_name)` for the operation's request body, if any."""
    body = op_data.get("requestBody")
    if not isinstance(body, dict):
        return None, None
    content = body.get("content")
    if not isinstance(content, dict) or not content:
        return None, None
    content_type = next(iter(content))
    raw_body_obj = raw_op.get("requestBody")
    raw_body: dict[str, Any] = raw_body_obj if isinstance(raw_body_obj, dict) else {}
    raw_content_obj = raw_body.get("content")
    raw_content: dict[str, Any] = raw_content_obj if isinstance(raw_content_obj, dict) else {}
    raw_entry = raw_content.get(content_type)
    raw_schema = raw_entry.get("schema") if isinstance(raw_entry, dict) else None
    schema_name = _schema_name(raw_schema) if isinstance(raw_schema, dict) else None
    if schema_name is None:
        resolved_entry = content.get(content_type)
        resolved_schema = resolved_entry.get("schema") if isinstance(resolved_entry, dict) else None
        if isinstance(resolved_schema, dict):
            title = resolved_schema.get("title")
            schema_name = title if isinstance(title, str) else None
    return content_type, schema_name


def _response_info(
    op_data: dict[str, Any],
    raw_op: dict[str, Any],
    *,
    apply_list_resolver: bool,
    list_response_resolver: ListResponseResolver | None,
) -> tuple[str | None, str | None]:
    """Return `(content_type, schema_name)` for the chosen 2xx response, if any."""
    responses = op_data.get("responses")
    if not isinstance(responses, dict):
        return None, None
    status = _pick_success_status(responses)
    if status is None:
        return None, None
    response = responses[status]
    if not isinstance(response, dict):
        return None, None
    content = response.get("content")
    if not isinstance(content, dict) or not content:
        return None, None
    content_type = next(iter(content))
    entry = content.get(content_type)
    schema = entry.get("schema") if isinstance(entry, dict) else None
    raw_responses_obj = raw_op.get("responses")
    raw_responses = raw_responses_obj if isinstance(raw_responses_obj, dict) else {}
    raw_response = raw_responses.get(status)
    raw_content = (
        raw_response.get("content") if isinstance(raw_response, dict) else None
    )
    raw_schema = (
        raw_content.get(content_type, {}).get("schema")
        if isinstance(raw_content, dict)
        else None
    )
    if apply_list_resolver and isinstance(schema, dict):
        schema = _apply_list_resolver(schema, list_response_resolver)
        # for list responses the ref-based name no longer reflects the item type, so
        # prefer the resolved schema's `title` when present.
        title = schema.get("title") if isinstance(schema, dict) else None
        if isinstance(title, str):
            return content_type, title
        return content_type, None
    schema_name = _schema_name(raw_schema) if isinstance(raw_schema, dict) else None
    if schema_name is None and isinstance(schema, dict):
        title = schema.get("title")
        schema_name = title if isinstance(title, str) else None
    return content_type, schema_name


def _apply_list_resolver(
    schema: dict[str, Any],
    list_response_resolver: ListResponseResolver | None,
) -> dict[str, Any]:
    """Run the user resolver first; fall through to the default heuristic."""
    if list_response_resolver is not None:
        candidate = list_response_resolver(schema)
        if isinstance(candidate, dict) and candidate is not schema:
            return candidate
    return default_list_response_resolver(schema)


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


def _merge_hint(sidecar_hint: str | None, spec_hint: str | None) -> str | None:
    """Return the sidecar hint when set, otherwise the spec hint, otherwise None."""
    if sidecar_hint is not None:
        return sidecar_hint
    return spec_hint


_PASCAL_SPLIT = re.compile(r"[-_\s]+")


def _pascal_case(token: str) -> str:
    """Convert a kebab-, snake-, or space-cased token to PascalCase."""
    parts = [p for p in _PASCAL_SPLIT.split(token) if p]
    return "".join(part[:1].upper() + part[1:] for part in parts) or token
