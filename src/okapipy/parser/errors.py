"""Error hierarchy raised by the okapipy structural parser."""

from __future__ import annotations


class ParserError(Exception):
    """Base class for all errors raised by the structural parser."""


class SpecLoadError(ParserError):
    """Raised when the OpenAPI document cannot be loaded, parsed, or validated."""


class SidecarFormatError(ParserError):
    """Raised when the disambiguation sidecar file cannot be parsed."""


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
            f"Run: okapipy nlp fetch {lang} --cache-dir {cache_dir}"
        )
        super().__init__(message)


class InvalidStructureError(ParserError):
    """Raised when the parsed structure violates the okapipy hierarchy rules.

    Currently this signals an attempt to attach an Action directly under a Namespace,
    which is not permitted: every Action must live under a Collection or a Resource.
    """
