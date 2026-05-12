"""Walk an OpenAPI document and produce a populated `APIModel` tree.

`build` is the single public entry. It iterates `paths`, classifies each segment
via `classify_segment`, attaches the corresponding node (`Namespace`,
`Collection`, `Resource`, `Singleton`, or `Action`) under its parent, and routes
the path-item's HTTP methods to operation slots on that node. The function
mutates the `APIModel` and its children in place — there are no draft or
wrapper types.

Three concerns live in this module:

* **Naming.** `contextual_name` joins the full breadcrumb of singular collection
  names accumulated so far, so `/organizations/{id}/datasources/{id}/force-reimport`
  yields `OrganizationDatasourceForceReimport`. Resource names use the
  breadcrumb for the same reason. `singularize` reduces a plural collection
  segment via the spaCy-backed lemmatizer.
* **Node attachment.** Each segment is mapped to a node kind by the classifier;
  `_attach` then either creates a new child or reuses an existing one with the
  same name. Namespace-level actions are valid (e.g. `/auth/login`); a path that
  attempts to place an action directly under a `Namespace` raises
  `InvalidStructureError` only when structurally impossible.
* **Operation routing.** GET/POST on a `Collection` map to `fetch`/`create`;
  GET/PUT/PATCH/DELETE on a `Resource` or `Singleton` map to
  `retrieve`/`update`/`partial_update`/`delete`. Operations that don't fit
  (e.g. `POST /users/{id}` with no `x-okapipy-kind: action` hint, PUT on a
  bare collection) are dropped with a warning rather than coerced into a
  synthetic action; synthetic actions exist only for explicit
  `x-okapipy-kind: action` opt-ins.

Schema names for `request_model` / `response_model` are recovered from the
unresolved `raw_spec` by reading the trailing segment of the original `$ref`,
falling back to the resolved schema's `title` when no ref is present.
`x-okapipy-exclude` skips whole paths (`"*"`) or specific methods
(`["DELETE", ...]`, case-insensitive); rules-file values override spec values
on every conflict.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from spacy.language import Language

from okapipy.parser.classifier import SegmentKind, classify_segment
from okapipy.parser.errors import (
    InvalidStructureError,
    UnmatchedNamespaceCollisionError,
)
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
    Singleton,
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

type ContainerNode = APIModel | Namespace | Collection | Resource | Singleton | Action


@dataclass(slots=True)
class _UnmatchedOp:
    """One operation that would otherwise be dropped by the routing table.

    Buffered during the path walk when `--unmatched <namespace>` is set
    and materialized into a synthetic `Action` after the main walk
    completes.
    """

    path: str
    method: str
    operation_id: str | None
    operation: Operation


def build(
    spec: dict[str, Any],
    rules: Rules,
    nlp: Language,
    *,
    strip_prefix: str | None = None,
    unmatched_namespace: str | None = None,
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
        unmatched_namespace: When set, operations that would otherwise be
            dropped by the routing table are retained as synthetic actions
            under a top-level namespace of this name. Raises
            `UnmatchedNamespaceCollisionError` when the name collides with
            an existing top-level node identifier.

    Returns:
        A populated APIModel.
    """
    api = APIModel()
    paths_obj = spec.get("paths") or {}
    if not paths_obj:
        if unmatched_namespace is not None:
            _attach_unmatched_namespace(api, unmatched_namespace, [])
        return api
    base = strip_prefix if strip_prefix is not None else detect_base_path(spec)
    paths = strip_base_path(paths_obj, base)
    ns_registry = root_namespaces(spec) | extra_namespaces(rules)
    spec_path_kinds = _collect_spec_path_kinds(paths)
    unmatched: list[_UnmatchedOp] | None = (
        [] if unmatched_namespace is not None else None
    )
    log.debug(
        "builder starting: %d paths, %d namespace hints, %d path-kind hints, base=%r",
        len(paths),
        len(ns_registry),
        len(spec_path_kinds),
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
                spec_path_kinds=spec_path_kinds,
                excluded_methods=excluded_methods,
                spec=spec,
                unmatched=unmatched,
            )
        except InvalidStructureError as exc:
            log.warning("skipping path %s: %s", path, exc)
    _apply_tag_descriptions(api, _collect_tag_descriptions(spec))
    if unmatched_namespace is not None:
        _attach_unmatched_namespace(api, unmatched_namespace, unmatched or [])
    return api


def _collect_tag_descriptions(spec: dict[str, Any]) -> dict[str, str]:
    """Index OpenAPI root `tags[]` by name → description.

    Skips entries that lack a name or a non-empty description; the resulting
    map is used to enrich namespace prose so synthesized namespaces (which
    carry no spec-level summary/description on their own) can still surface
    the description the API author wrote on the matching tag.
    """
    tags = spec.get("tags")
    if not isinstance(tags, list):
        return {}
    out: dict[str, str] = {}
    for entry in tags:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        description = entry.get("description")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(description, str) or not description.strip():
            continue
        out[name] = description.strip()
    return out


def _apply_tag_descriptions(api: APIModel, tag_descriptions: dict[str, str]) -> None:
    """Copy matching tag descriptions onto namespaces that have none.

    Namespaces are synthesized from path segments and start with `description=None`;
    when a tag's `name` exactly matches a namespace's `name` and the namespace
    has no description yet, the tag's description is copied over. Already-set
    descriptions are never overwritten.
    """
    if not tag_descriptions:
        return
    for ns in api.namespaces:
        _apply_tag_descriptions_to_namespace(ns, tag_descriptions)


def _apply_tag_descriptions_to_namespace(
    ns: Namespace, tag_descriptions: dict[str, str]
) -> None:
    """Recursive sibling of `_apply_tag_descriptions` that walks one namespace subtree."""
    if ns.description is None:
        candidate = tag_descriptions.get(ns.name)
        if candidate is not None:
            ns.description = candidate
    for child in ns.namespaces:
        _apply_tag_descriptions_to_namespace(child, tag_descriptions)


def _collect_spec_path_kinds(paths: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Index path-item-level `x-okapipy-kind` hints by cumulative path.

    Keyed by the path **without** a leading slash, matching the cumulative-path
    form the classifier compares against. The map propagates a path-item-level
    hint (e.g. `x-okapipy-kind: singleton` on `/me`) to every other path that
    walks through the same prefix (e.g. `/me/refresh`), so intermediate
    segments resolve to the right kind.
    """
    collected: dict[str, str] = {}
    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        hint = path_item_extension(path_item)
        if hint is None:
            continue
        collected[raw_path.lstrip("/")] = hint
    return collected


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

    Every singular collection name and singleton segment accumulated in `breadcrumb`
    is concatenated, then the PascalCase form of `current` is appended. With an
    empty breadcrumb, only `PascalCase(current)` is returned.

    Namespaces never enter the breadcrumb — they're pure folders and carry no
    semantic ownership. Singletons do, because the elements they host belong
    to them (the orders under `/me` are *Me's* orders, not generic orders),
    which also prevents file-name collisions when a top-level collection and
    a singleton sub-collection share a segment (`/orders` vs `/me/orders`).

    Examples:
        contextual_name([], "orders") == "Orders"
        contextual_name(["Order"], "lines") == "OrderLines"
        contextual_name(["Me"], "orders") == "MeOrders"
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
    spec_path_kinds: dict[str, str],
    excluded_methods: set[str],
    spec: dict[str, Any],
    unmatched: list[_UnmatchedOp] | None,
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
            hint = _merge_hint(
                path_item_hint(rules, "/" + cumulative_path),
                spec_path_kinds.get(cumulative_path),
            )
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
        spec=spec,
        unmatched=unmatched,
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
        if not isinstance(cursor, (APIModel, Namespace, Resource, Singleton)):
            raise InvalidStructureError(
                f"collection segment {segment!r} cannot be attached under {type(cursor).__name__}"
            )
        name = contextual_name(breadcrumb, segment)
        collections_list = cursor.collections
        existing_col = next((c for c in collections_list if c.name == name), None)
        if existing_col is None:
            existing_col = Collection(name=name, path=cumulative_path)
            collections_list.append(existing_col)
        new_breadcrumb = [*breadcrumb, _pascal_case(singularize(segment, nlp))]
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
    if kind is SegmentKind.SINGLETON:
        if not isinstance(
            cursor, (APIModel, Namespace, Collection, Resource, Singleton)
        ):
            raise InvalidStructureError(
                f"singleton {segment!r} cannot be attached under {type(cursor).__name__}"
            )
        name = contextual_name(breadcrumb, segment)
        existing_sing = next((s for s in cursor.singletons if s.name == name), None)
        if existing_sing is None:
            existing_sing = Singleton(name=name, path=cumulative_path)
            cursor.singletons.append(existing_sing)
        new_breadcrumb = [*breadcrumb, _pascal_case(singularize(segment, nlp))]
        return existing_sing, new_breadcrumb
    if kind is SegmentKind.ACTION:
        if not isinstance(
            cursor, (APIModel, Namespace, Collection, Resource, Singleton)
        ):
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
    spec: dict[str, Any],
    unmatched: list[_UnmatchedOp] | None,
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
            spec=spec,
        )
        operation_id = op_data.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip():
            operation_id = None
        _route(
            cursor=cursor,
            terminal_kind=terminal_kind,
            method=method,
            operation=operation,
            operation_id=operation_id,
            action_path=action_path,
            is_action_method=is_action_method,
            unmatched=unmatched,
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
    operation_id: str | None,
    action_path: str,
    is_action_method: bool,
    unmatched: list[_UnmatchedOp] | None,
) -> None:
    """Place a single Operation on the terminal node based on its kind and method."""
    if isinstance(cursor, (APIModel, Namespace)):
        _drop_or_buffer(
            unmatched=unmatched,
            method=method,
            action_path=action_path,
            operation_id=operation_id,
            operation=operation,
            reason=(
                "a bare namespace path has no operation slot. Mark it with "
                "x-okapipy-kind to expose it as a collection, singleton, or action."
            ),
        )
        return
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
            _drop_or_buffer(
                unmatched=unmatched,
                method=method,
                action_path=action_path,
                operation_id=operation_id,
                operation=operation,
                reason=(
                    f"method has no canonical slot on collection {cursor.name!r} "
                    f"and the operation does not fit the namespace/collection/"
                    f"resource/action hierarchy. Mark it with x-okapipy-kind: "
                    f"action to keep it."
                ),
            )
        return
    if isinstance(cursor, (Resource, Singleton)):
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
            kind_label = "resource" if isinstance(cursor, Resource) else "singleton"
            _drop_or_buffer(
                unmatched=unmatched,
                method=method,
                action_path=action_path,
                operation_id=operation_id,
                operation=operation,
                reason=(
                    f"method has no canonical slot on {kind_label} "
                    f"{cursor.name!r} and the operation does not fit the "
                    f"namespace/collection/resource/singleton/action hierarchy. "
                    f"Mark it with x-okapipy-kind: action to keep it."
                ),
            )


def _drop_or_buffer(
    *,
    unmatched: list[_UnmatchedOp] | None,
    method: str,
    action_path: str,
    operation_id: str | None,
    operation: Operation,
    reason: str,
) -> None:
    """Either log+drop the operation or stash it for the unmatched namespace.

    When `unmatched` is `None` (the flag is off) this preserves the prior
    behaviour: emit a `logging.warning(...)` and discard the operation.
    When `unmatched` is a list the operation is appended verbatim and the
    `_attach_unmatched_namespace` post-walk pass synthesizes an `Action`
    from it.
    """
    if unmatched is not None:
        unmatched.append(
            _UnmatchedOp(
                path=action_path,
                method=method,
                operation_id=operation_id,
                operation=operation,
            )
        )
        return
    log.warning("skipping %s %s: %s", method.upper(), action_path, reason)


def _attach_unmatched_namespace(
    api: APIModel,
    requested: str,
    unmatched: list[_UnmatchedOp],
) -> None:
    """Synthesize a top-level Namespace holding one Action per unmatched op.

    Validates that `requested` does not collide with any existing top-level
    node identifier (snake_case form) before adding anything to the tree.
    When `unmatched` is empty the collision check still runs — so a stale
    flag against a clean spec surfaces — but no namespace is attached.

    Raises:
        UnmatchedNamespaceCollisionError: when `requested` matches the
            snake_case identifier of an existing top-level Namespace,
            Collection, Singleton, or Action.
    """
    normalized = _snake_case(requested)
    if not normalized:
        raise UnmatchedNamespaceCollisionError(requested, "namespace", requested)
    _check_unmatched_collision(api, requested, normalized)
    if not unmatched:
        return
    namespace = Namespace(name=requested)
    used_attrs: set[str] = set()
    used_classes: set[str] = set()
    for entry in unmatched:
        attr_base, class_base = _unmatched_action_names(entry)
        attr_name, class_name = _disambiguate_unmatched(
            attr_base, class_base, used_attrs, used_classes
        )
        if attr_name != attr_base:
            log.warning(
                "unmatched operationId %r already in use; emitting as %r",
                attr_base,
                attr_name,
            )
        used_attrs.add(attr_name)
        used_classes.add(class_name)
        namespace.actions.append(
            Action(
                name=class_name,
                path=entry.path,
                attr_override=attr_name,
                operations=[entry.operation],
            )
        )
    api.namespaces.append(namespace)


def _check_unmatched_collision(api: APIModel, requested: str, normalized: str) -> None:
    """Raise if `normalized` collides with any top-level identifier in `api`."""
    for ns in api.namespaces:
        if _snake_case(ns.name) == normalized:
            raise UnmatchedNamespaceCollisionError(requested, "namespace", ns.name)
    for coll in api.collections:
        if _snake_case(_last_non_template_segment(coll.path)) == normalized:
            raise UnmatchedNamespaceCollisionError(requested, "collection", coll.name)
    for sing in api.singletons:
        if _snake_case(_last_non_template_segment(sing.path)) == normalized:
            raise UnmatchedNamespaceCollisionError(requested, "singleton", sing.name)
    for act in api.actions:
        attr = act.attr_override or _last_non_template_segment(act.path)
        if _snake_case(attr) == normalized:
            raise UnmatchedNamespaceCollisionError(requested, "action", act.name)


def _unmatched_action_names(entry: _UnmatchedOp) -> tuple[str, str]:
    """Return `(attr_name, class_name)` for one unmatched op.

    `operationId` drives both names when declared. The fallback derives
    `<method>_<sanitized_path>` (attr) and `<Method><PascalCasePath>`
    (class) — the same pattern flat-style generators use when no
    `operationId` is present.
    """
    if entry.operation_id is not None:
        return _snake_case(entry.operation_id), _pascal_case(entry.operation_id)
    sanitized = _sanitize_path_for_id(entry.path)
    attr = f"{entry.method}_{sanitized}" if sanitized else entry.method
    pascal_path = _pascal_case(sanitized) if sanitized else ""
    class_name = (
        f"{entry.method.capitalize()}{pascal_path}" or entry.method.capitalize()
    )
    return attr, class_name


def _disambiguate_unmatched(
    attr_base: str,
    class_base: str,
    used_attrs: set[str],
    used_classes: set[str],
) -> tuple[str, str]:
    """Suffix `_N` / `N` until both attr and class names are unique."""
    if attr_base not in used_attrs and class_base not in used_classes:
        return attr_base, class_base
    counter = 2
    while True:
        attr_candidate = f"{attr_base}_{counter}"
        class_candidate = f"{class_base}{counter}"
        if attr_candidate not in used_attrs and class_candidate not in used_classes:
            return attr_candidate, class_candidate
        counter += 1


def _sanitize_path_for_id(path: str) -> str:
    """Turn `/users/{id}/admin` into `users_id_admin` for fallback naming.

    Path parameters keep their inner name (so `{org_id}` becomes `org_id`)
    rather than being dropped — that way the fallback identifier still
    distinguishes `/users/{id}` from `/users/{other_id}` when both are
    unmatched.
    """
    parts: list[str] = []
    for segment in path.split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}"):
            parts.append(segment[1:-1])
        else:
            parts.append(segment)
    return _snake_case("_".join(parts))


def _last_non_template_segment(path: str) -> str:
    """Return the last `/`-separated segment of `path` that is not `{template}`."""
    for segment in reversed([s for s in path.split("/") if s]):
        if segment.startswith("{") and segment.endswith("}"):
            continue
        return segment
    return ""


def _attach_synthetic_action(
    parent: Collection | Resource | Singleton,
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
    spec: dict[str, Any],
) -> Operation:
    """Build an Operation from one method entry, reading schema names from `$ref`s."""
    summary = op_data.get("summary")
    description = op_data.get("description")
    request_content_type, request_model, request_model_members = _request_info(op_data)
    response_content_type, response_model, item_model, response_headers = (
        _response_info(op_data, spec)
    )
    return Operation(
        method=method.upper(),
        summary=summary if isinstance(summary, str) else None,
        description=description if isinstance(description, str) else None,
        request_content_type=request_content_type,
        request_model=request_model,
        request_model_members=request_model_members,
        response_content_type=response_content_type,
        response_model=response_model,
        item_model=item_model,
        response_headers=response_headers,
        pagination_supported=pagination_supported,
    )


def _request_info(
    op_data: dict[str, Any],
) -> tuple[str | None, str | None, list[str]]:
    """Return `(content_type, schema_name, union_members)` for the request body, if any.

    `union_members` is non-empty when the body schema is an inline `anyOf` /
    `oneOf` whose non-null members are all `$ref`s and there are 2+ of them —
    the generator renders the body parameter as a `Member1 | Member2 | ...`
    union. A single non-null `$ref` member (e.g. `[$ref, type: null]`) collapses
    back to that single ref. When neither case applies the schema's `$ref` /
    `title` fallback drives `schema_name`.
    """
    body = op_data.get("requestBody")
    if not isinstance(body, dict):
        return None, None, []
    content = body.get("content")
    if not isinstance(content, dict) or not content:
        return None, None, []
    content_type = next(iter(content))
    entry = content.get(content_type)
    schema = entry.get("schema") if isinstance(entry, dict) else None
    members = _union_member_names(schema)
    if len(members) >= 2:
        return content_type, None, members
    if len(members) == 1:
        return content_type, members[0], []
    return content_type, _name_from_schema(schema), []


def _union_member_names(schema: Any) -> list[str]:
    """Return the deduped `$ref` trailing names of an inline `anyOf` / `oneOf`.

    Empty when the schema isn't a union, when any non-null member fails to be
    a `$ref`, or when there are no non-null members. Caller decides what to
    do with the count — a single name still rides through as a regular ref.
    """
    if not isinstance(schema, dict):
        return []
    union = schema.get("anyOf") or schema.get("oneOf")
    if not isinstance(union, list) or not union:
        return []
    names: list[str] = []
    for member in union:
        if not isinstance(member, dict):
            return []
        if member.get("type") == "null":
            continue
        ref_name = _schema_name(member)
        if ref_name is None:
            return []
        names.append(ref_name)
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


def _response_info(
    op_data: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[str | None, str | None, str | None, list[str]]:
    """Return `(content_type, schema_name, item_name, header_names)` for the chosen 2xx response.

    `schema_name` always names the literal response body schema as declared (the
    envelope, when one wraps a list). `item_name` names the inner element schema
    when the response is list-shaped — either a plain `type: array` or an object
    with a known data-array property (`items`, `data`, `results`, `records`,
    `entries`); `None` otherwise. The generator uses `item_name` so paginated
    iteration yields typed model instances. `spec` is consulted only for one-hop
    `$ref` resolution into `components.schemas` so envelope refs surface their
    inner item type.
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
        _item_name_from_schema(schema, spec),
        headers,
    )


_ENVELOPE_DATA_KEYS = ("items", "data", "results", "records", "entries")


def _item_name_from_schema(schema: Any, spec: dict[str, Any]) -> str | None:
    """Return the inner item schema name for a list-shaped response, or `None`.

    Recognised shapes: plain `type: array` (item is `schema.items`) and object
    schemas with one of the conventional data-array properties (`items`, `data`,
    `results`, `records`, `entries`). When the response schema is a `$ref`,
    one hop is followed into `components.schemas` so envelope refs (e.g.
    `LimitOffsetPage_OrganizationRead_`) surface their item type without
    forcing the parser to fully resolve the spec.
    """
    if not isinstance(schema, dict):
        return None
    resolved = _resolve_one_ref(schema, spec) if "$ref" in schema else schema
    if not isinstance(resolved, dict):
        return None
    if resolved.get("type") == "array":
        return _name_from_schema(resolved.get("items"))
    props = resolved.get("properties")
    if not isinstance(props, dict):
        return None
    for key in _ENVELOPE_DATA_KEYS:
        entry = props.get(key)
        if isinstance(entry, dict) and entry.get("type") == "array":
            return _name_from_schema(entry.get("items"))
    return None


def _resolve_one_ref(schema: dict[str, Any], spec: dict[str, Any]) -> Any:
    """Resolve a single `#/components/schemas/Foo` ref against `spec`.

    Returns the target schema dict, or `None` if the ref points outside
    `components.schemas` or to a missing entry. Only one hop is followed —
    deeper resolution would risk infinite loops on self-referential schemas.
    """
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return None
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        return None
    name = ref[len(prefix) :]
    components = spec.get("components")
    if not isinstance(components, dict):
        return None
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return None
    return schemas.get(name)


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
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _snake_case(value: str) -> str:
    """Convert PascalCase / camelCase / kebab-case input to snake_case.

    Mirrors the generator's `templating.snake_case` so the parser's
    collision check sees the same identifier the generator will emit.
    Kept local rather than imported to preserve the parser's
    independence from the generator package.
    """
    normalized = value.replace(".", "_dot_").replace("-", "_").replace(" ", "_")
    snake = _CAMEL_BOUNDARY.sub("_", normalized).lower()
    return re.sub(r"_+", "_", snake).strip("_")


def _pascal_case(token: str) -> str:
    """Convert a kebab-, snake-, or space-cased token to PascalCase.

    A literal ``.`` is expanded to the word ``Dot`` so segments like
    ``.well-known`` produce valid Python identifiers (``DotWellKnown``)
    rather than the syntactically invalid ``.WellKnown``.
    """
    parts = [p for p in _PASCAL_SPLIT.split(token.replace(".", "-dot-")) if p]
    return "".join(part[:1].upper() + part[1:] for part in parts) or token
