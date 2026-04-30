"""Pydantic models that describe the parsed OpenAPI structural tree.

The tree has four node kinds — Namespace, Collection, Resource, Action — and one leaf
container, Operation. Models are mutable on purpose: the builder appends to the lists
during construction.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Operation(BaseModel):
    """A single HTTP operation declared on a path.

    `response_model` names the literal 2xx response body schema (the envelope when
    the response wraps a list, or the resource itself for single-item responses).
    `item_model` names the **inner** schema of a list envelope when one is detected
    (plain `type: array`, or an object with an `items`/`data`/`results`/`records`/
    `entries` array property). The generator uses it so paginated iteration can
    yield typed model instances instead of raw dicts; left as `None` when the
    response isn't list-shaped or the item schema is anonymous.

    `pagination_supported` defaults to `True` and is only meaningful on
    collection-fetch operations; the generator decides what to do with it on other
    operations. `filter_supported` and `sort_supported` default to `False` and will
    be flipped on by future `x-okapipy-filter` / `x-okapipy-sort` extensions; they
    drive whether the generator emits `filter()` / `order_by()` on the collection.
    `response_headers` lists the names of headers declared on the chosen 2xx
    response, useful to the generator for detecting `Link`, `X-Total-Count`, etc.
    """

    method: str
    summary: str | None = None
    description: str | None = None
    request_content_type: str | None = None
    request_model: str | None = None
    response_content_type: str | None = None
    response_model: str | None = None
    item_model: str | None = None
    response_headers: list[str] = Field(default_factory=list)
    pagination_supported: bool = True
    filter_supported: bool = False
    sort_supported: bool = False


class Action(BaseModel):
    """A non-CRUD endpoint identified by a verb-phrase path segment."""

    name: str
    path: str
    summary: str | None = None
    description: str | None = None
    operations: list[Operation] = Field(default_factory=list)


class Resource(BaseModel):
    """The single-item endpoint of a collection (the segment after `{id}`)."""

    name: str
    path: str
    summary: str | None = None
    description: str | None = None
    retrieve: Operation | None = None
    update: Operation | None = None
    partial_update: Operation | None = None
    delete: Operation | None = None
    actions: list[Action] = Field(default_factory=list)
    collections: list[Collection] = Field(default_factory=list)


class Collection(BaseModel):
    """A plural endpoint that fetches a list, creates, and contains a Resource."""

    name: str
    path: str
    summary: str | None = None
    description: str | None = None
    fetch: Operation | None = None
    create: Operation | None = None
    resource: Resource | None = None
    actions: list[Action] = Field(default_factory=list)


class Namespace(BaseModel):
    """A folder-like grouping of sub-namespaces and collections."""

    name: str
    summary: str | None = None
    description: str | None = None
    namespaces: list[Namespace] = Field(default_factory=list)
    collections: list[Collection] = Field(default_factory=list)


class APIModel(BaseModel):
    """The root of the parsed structural tree.

    The root holds both namespaces and top-level collections. The latter exists
    because real-world OpenAPI documents commonly expose collections directly under
    `/`, with no folder-style namespace prefix (e.g. `/orders`, `/users`).
    """

    namespaces: list[Namespace] = Field(default_factory=list)
    collections: list[Collection] = Field(default_factory=list)


Resource.model_rebuild()
Collection.model_rebuild()
Namespace.model_rebuild()
APIModel.model_rebuild()
