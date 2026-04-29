"""Structural OpenAPI parser: turns flat OpenAPI paths into a hierarchical tree.

The tree has four node kinds — Namespace, Collection, Resource, Action — plus a leaf
Operation container. The single public entry is `parse`.
"""

from okapipy.parser.api import DEFAULT_NLP_CACHE_DIR, parse
from okapipy.parser.errors import (
    InvalidStructureError,
    NlpModelMissingError,
    ParserError,
    SidecarFormatError,
    SpecLoadError,
)
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Operation,
    Resource,
)

__all__ = [
    "DEFAULT_NLP_CACHE_DIR",
    "APIModel",
    "Action",
    "Collection",
    "InvalidStructureError",
    "Namespace",
    "NlpModelMissingError",
    "Operation",
    "ParserError",
    "Resource",
    "SidecarFormatError",
    "SpecLoadError",
    "parse",
]
