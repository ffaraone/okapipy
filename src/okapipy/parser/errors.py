"""Error hierarchy raised by the okapipy structural parser."""

from __future__ import annotations


class ParserError(Exception):
    """Base class for all errors raised by the structural parser."""


class SpecLoadError(ParserError):
    """Raised when the OpenAPI document cannot be loaded, parsed, or validated."""


class RulesFormatError(ParserError):
    """Raised when the rules file cannot be parsed."""


class NlpModelMissingError(ParserError):
    """Raised when the requested spaCy model is unavailable and cannot be downloaded.

    Attributes:
        lang: The ISO language code that was requested.
        cache_dir: The directory the loader looked in (and would have downloaded into).
    """

    def __init__(self, lang: str, cache_dir: str) -> None:
        self.lang = lang
        self.cache_dir = cache_dir
        message = (
            f"spaCy model for language '{lang}' is not available under {cache_dir}. "
            f"Run: okapipy fetch-language {lang} --cache-dir {cache_dir}"
        )
        super().__init__(message)


class InvalidStructureError(ParserError):
    """Raised when the parsed structure violates the okapipy hierarchy rules.

    Currently this signals an attempt to attach an Action directly under a Namespace,
    which is not permitted: every Action must live under a Collection or a Resource.
    """


class UnmatchedNamespaceCollisionError(ParserError):
    """Raised when `--unmatched <name>` collides with an existing top-level node.

    The synthesized container for unmatched operations must not share a
    snake_case identifier with any top-level Namespace, Collection,
    Singleton, or Action: that would produce two attributes with the same
    name on the generated client class. The caller picks a different name.

    Attributes:
        requested: The name passed via `unmatched_namespace`.
        conflict_kind: The kind of the conflicting node (`"namespace"`,
            `"collection"`, `"singleton"`, or `"action"`).
        conflict_name: The original (pre-snake_case) name of the
            conflicting top-level node.
    """

    def __init__(self, requested: str, conflict_kind: str, conflict_name: str) -> None:
        self.requested = requested
        self.conflict_kind = conflict_kind
        self.conflict_name = conflict_name
        message = (
            f"--unmatched namespace {requested!r} collides with top-level "
            f"{conflict_kind} {conflict_name!r}. Pick a different name."
        )
        super().__init__(message)
