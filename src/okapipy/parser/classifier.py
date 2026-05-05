"""Single-segment classifier — phase 3 step 1 of the parser pipeline.

The classifier converts one path segment into a `SegmentKind`. It consults, in order:
the path-parameter shape, an explicit hint (rules > spec extension), the namespace
registry (rules ∪ spec), and finally the spaCy-derived NLP signal.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from spacy.language import Language

from okapipy.parser.nlp import analyze_segment

log = logging.getLogger(__name__)


class SegmentKind(StrEnum):
    """The five roles a path segment can play in the structural tree.

    `SINGLETON` is reachable only via an explicit `x-okapipy-kind: singleton`
    hint (in the spec or rules). NLP cannot reliably distinguish a singleton
    (`/me`, `/health`) from a singular-noun namespace, so the classifier never
    derives `SINGLETON` from heuristics.
    """

    NAMESPACE = "namespace"
    COLLECTION = "collection"
    ACTION = "action"
    SINGLETON = "singleton"
    RESOURCE_ID = "resource_id"


def classify_segment(
    *,
    segment: str,
    cumulative_path: str,
    parent_kind: SegmentKind | None,
    nlp: Language,
    ns_registry: set[str],
    extension_hint: str | None,
) -> SegmentKind:
    """Classify a single path segment into one of four kinds.

    Args:
        segment: The raw segment as it appears between `/` characters.
        cumulative_path: The path so far, joined from previous segments without a
            leading or trailing slash; used for the namespace-registry lookup.
        parent_kind: The kind of the previous segment, or None when at the root.
        nlp: A loaded spaCy pipeline used for POS and morphology.
        ns_registry: The union of namespace paths declared by the spec and rules.
        extension_hint: A pre-merged `x-okapipy-kind` hint with rules precedence; one of
            the five kind names, or None.

    Returns:
        The classified `SegmentKind`.
    """
    kind = _classify(
        segment=segment,
        cumulative_path=cumulative_path,
        parent_kind=parent_kind,
        nlp=nlp,
        ns_registry=ns_registry,
        extension_hint=extension_hint,
    )
    log.debug("classified segment %r at %r as %s", segment, cumulative_path, kind.value)
    return kind


def _classify(
    *,
    segment: str,
    cumulative_path: str,
    parent_kind: SegmentKind | None,
    nlp: Language,
    ns_registry: set[str],
    extension_hint: str | None,
) -> SegmentKind:
    """Inner classifier — runs the precedence chain and returns a `SegmentKind`."""
    if "{" in segment and "}" in segment:
        return SegmentKind.RESOURCE_ID
    if extension_hint is not None:
        kind = _hint_to_kind(extension_hint)
        if kind is not None:
            return kind
    if cumulative_path in ns_registry:
        return SegmentKind.NAMESPACE
    info = analyze_segment(nlp, segment)
    if info.is_verb_phrase:
        return SegmentKind.ACTION
    if info.is_plural:
        return SegmentKind.COLLECTION
    if info.is_singular_or_unknown:
        if parent_kind in (None, SegmentKind.NAMESPACE):
            return SegmentKind.NAMESPACE
        return SegmentKind.COLLECTION
    log.warning(
        "classifier fell through for segment %r at %r; defaulting to collection",
        segment,
        cumulative_path,
    )
    return SegmentKind.COLLECTION


def _hint_to_kind(hint: str) -> SegmentKind | None:
    """Map an `x-okapipy-kind` string to a `SegmentKind`, or None when the hint is invalid."""
    try:
        return SegmentKind(hint)
    except ValueError:
        return None
