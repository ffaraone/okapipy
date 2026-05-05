"""Table-driven tests covering every branch of the segment classifier."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from okapipy.parser.classifier import SegmentKind, classify_segment
from okapipy.parser.nlp import SegmentInfo


def _stub_segment(mocker: MockerFixture, info: SegmentInfo) -> None:
    """Force `analyze_segment` to return a fixed `SegmentInfo` for the test."""
    mocker.patch("okapipy.parser.classifier.analyze_segment", return_value=info)


def test_path_parameter_segment_classified_as_resource_id(
    mocker: MockerFixture,
) -> None:
    """A segment containing `{` and `}` short-circuits to RESOURCE_ID."""
    nlp = mocker.Mock(name="Language")

    kind = classify_segment(
        segment="{id}",
        cumulative_path="orders",
        parent_kind=SegmentKind.COLLECTION,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=None,
    )

    assert kind is SegmentKind.RESOURCE_ID


def test_extension_hint_overrides_nlp(mocker: MockerFixture) -> None:
    """An explicit `x-okapipy-kind` hint wins over the NLP signal."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("orders", False, True, False))

    kind = classify_segment(
        segment="orders",
        cumulative_path="orders",
        parent_kind=None,
        nlp=nlp,
        ns_registry=set(),
        extension_hint="action",
    )

    assert kind is SegmentKind.ACTION


def test_namespace_registry_marks_segment_as_namespace(mocker: MockerFixture) -> None:
    """A cumulative path present in the registry is forced to NAMESPACE."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("commerce", False, False, True))

    kind = classify_segment(
        segment="commerce",
        cumulative_path="commerce",
        parent_kind=None,
        nlp=nlp,
        ns_registry={"commerce"},
        extension_hint=None,
    )

    assert kind is SegmentKind.NAMESPACE


def test_verb_phrase_segment_classified_as_action(mocker: MockerFixture) -> None:
    """A segment whose NLP signal is `is_verb_phrase` becomes ACTION."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("submit", True, False, False))

    kind = classify_segment(
        segment="submit",
        cumulative_path="orders/{id}/submit",
        parent_kind=SegmentKind.RESOURCE_ID,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=None,
    )

    assert kind is SegmentKind.ACTION


def test_plural_noun_segment_classified_as_collection(mocker: MockerFixture) -> None:
    """A plural noun without an extension hint becomes a COLLECTION."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("orders", False, True, False))

    kind = classify_segment(
        segment="orders",
        cumulative_path="orders",
        parent_kind=None,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=None,
    )

    assert kind is SegmentKind.COLLECTION


def test_singular_unknown_at_root_becomes_namespace(mocker: MockerFixture) -> None:
    """A singular or unknown word at the document root becomes a NAMESPACE."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("commerce", False, False, True))

    kind = classify_segment(
        segment="commerce",
        cumulative_path="commerce",
        parent_kind=None,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=None,
    )

    assert kind is SegmentKind.NAMESPACE


def test_singular_unknown_under_collection_becomes_collection(
    mocker: MockerFixture,
) -> None:
    """A singular noun nested under a collection is treated as a sub-collection."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("staff", False, False, True))

    kind = classify_segment(
        segment="staff",
        cumulative_path="commerce/staff",
        parent_kind=SegmentKind.COLLECTION,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=None,
    )

    assert kind is SegmentKind.COLLECTION


def test_invalid_extension_hint_falls_through_to_nlp(mocker: MockerFixture) -> None:
    """A garbage hint is ignored and the NLP signal is used instead."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("orders", False, True, False))

    kind = classify_segment(
        segment="orders",
        cumulative_path="orders",
        parent_kind=None,
        nlp=nlp,
        ns_registry=set(),
        extension_hint="not-a-real-kind",
    )

    assert kind is SegmentKind.COLLECTION


def test_classifier_logs_warning_when_no_signal_is_set(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    """When NLP returns no positive signal, the classifier logs a warning and falls through."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("opaque", False, False, False))

    with caplog.at_level("WARNING"):
        kind = classify_segment(
            segment="opaque",
            cumulative_path="opaque",
            parent_kind=SegmentKind.COLLECTION,
            nlp=nlp,
            ns_registry=set(),
            extension_hint=None,
        )

    assert kind is SegmentKind.COLLECTION
    assert "fell through" in caplog.text


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("namespace", SegmentKind.NAMESPACE),
        ("collection", SegmentKind.COLLECTION),
        ("action", SegmentKind.ACTION),
        ("singleton", SegmentKind.SINGLETON),
        ("resource_id", SegmentKind.RESOURCE_ID),
    ],
)
def test_extension_hint_recognizes_each_kind(
    hint: str, expected: SegmentKind, mocker: MockerFixture
) -> None:
    """Each of the five legal hint strings maps to the matching SegmentKind."""
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("anything", False, False, True))

    kind = classify_segment(
        segment="anything",
        cumulative_path="anything",
        parent_kind=None,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=hint,
    )

    assert kind is expected


def test_singleton_kind_only_reachable_via_explicit_hint(
    mocker: MockerFixture,
) -> None:
    """Without a `singleton` hint, a singular-noun segment falls back to NAMESPACE.

    NLP cannot disambiguate `/me` from a singular-noun namespace, so the
    classifier never derives SINGLETON from heuristics; users must opt in via
    `x-okapipy-kind: singleton`.
    """
    nlp = mocker.Mock(name="Language")
    _stub_segment(mocker, SegmentInfo("me", False, False, True))

    kind = classify_segment(
        segment="me",
        cumulative_path="me",
        parent_kind=None,
        nlp=nlp,
        ns_registry=set(),
        extension_hint=None,
    )

    assert kind is SegmentKind.NAMESPACE
