"""Structural OpenAPI parser: turns flat OpenAPI paths into a hierarchical tree.

The tree has five node kinds — Namespace, Collection, Resource, Singleton, Action —
plus a leaf Operation container. The single public entry is `parse`.
"""

from okapipy.parser.api import DEFAULT_NLP_CACHE_DIR, parse
from okapipy.parser.errors import (
    InvalidStructureError,
    NlpModelMissingError,
    ParserError,
    RulesFormatError,
    SpecLoadError,
    UnmatchedNamespaceCollisionError,
)
from okapipy.parser.model import (
    Action,
    APIModel,
    Collection,
    Namespace,
    Operation,
    Resource,
    Singleton,
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
    "RulesFormatError",
    "Singleton",
    "SpecLoadError",
    "UnmatchedNamespaceCollisionError",
    "parse",
]
