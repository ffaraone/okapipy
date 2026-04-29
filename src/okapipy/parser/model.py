"""Pydantic models that describe the parsed OpenAPI structural tree.

The tree has four node kinds — Namespace, Collection, Resource, Action — and one leaf
container, Operation. Models are mutable on purpose: the builder appends to the lists
during construction.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Operation(BaseModel):
    """A single HTTP operation declared on a path."""

    method: str
    summary: str | None = None
    description: str | None = None
    request_content_type: str | None = None
    request_model: str | None = None
    response_content_type: str | None = None
    response_model: str | None = None


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
