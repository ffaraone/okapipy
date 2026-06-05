"""Tests for `parser.source_context` — parser-log mount tagging."""

from __future__ import annotations

import logging

import pytest
from spacy.language import Language

from okapipy.parser.builder import build
from okapipy.parser.rules import Rules
from okapipy.parser.source_context import source_context


def _spec_with_paths(paths: list[str]) -> dict[str, object]:
    """Return a minimal OpenAPI document with one trivial GET per path."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1.0"},
        "paths": {
            p: {"get": {"responses": {"200": {"description": "OK"}}}} for p in paths
        },
    }


def test_records_carry_tag_when_context_set(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """Records emitted inside `source_context("auth")` prepend `[auth]`."""
    spec = _spec_with_paths(
        ["/auth/v2/tokens", "/auth/v2/users", "/auth/v2/roles", "/auth/v2/sessions"]
    )

    with caplog.at_level(logging.WARNING), source_context("auth"):
        build(spec, Rules(), english_nlp)

    parser_records = [r for r in caplog.records if r.name.startswith("okapipy.parser")]
    assert parser_records, "expected at least one parser warning"
    assert all(r.getMessage().startswith("[auth] ") for r in parser_records)


def test_records_have_no_tag_outside_context(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """Outside any `source_context`, parser records pass through unchanged."""
    spec = _spec_with_paths(
        ["/auth/v2/tokens", "/auth/v2/users", "/auth/v2/roles", "/auth/v2/sessions"]
    )

    with caplog.at_level(logging.WARNING):
        build(spec, Rules(), english_nlp)

    parser_records = [r for r in caplog.records if r.name.startswith("okapipy.parser")]
    assert parser_records
    assert not any("[" in r.getMessage().split(" ", 1)[0] for r in parser_records)


def test_nested_context_uses_innermost_tag(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """`source_context` nests like any other ContextVar manager."""
    spec = _spec_with_paths(
        ["/x/v2/tokens", "/x/v2/users", "/x/v2/roles", "/x/v2/sessions"]
    )

    with (
        caplog.at_level(logging.WARNING),
        source_context("outer"),
        source_context("inner"),
    ):
        build(spec, Rules(), english_nlp)

    inner_records = [r for r in caplog.records if r.name.startswith("okapipy.parser")]
    assert inner_records
    assert all(r.getMessage().startswith("[inner] ") for r in inner_records)


def test_tag_survives_percent_signs_in_source(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """A `%` in the source tag does not collide with format placeholders.

    The factory resolves `record.msg % record.args` before prepending,
    so `[%-encoded]` cannot interpolate as a format spec.
    """
    spec = _spec_with_paths(
        ["/auth/v2/tokens", "/auth/v2/users", "/auth/v2/roles", "/auth/v2/sessions"]
    )

    with caplog.at_level(logging.WARNING), source_context("foo%20bar"):
        build(spec, Rules(), english_nlp)

    parser_records = [r for r in caplog.records if r.name.startswith("okapipy.parser")]
    assert parser_records
    assert all("[foo%20bar]" in r.getMessage() for r in parser_records)


def test_non_parser_loggers_are_untouched(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A logger outside `okapipy.parser` keeps its message verbatim."""
    log = logging.getLogger("okapipy.generator.test_only")

    with caplog.at_level(logging.WARNING), source_context("auth"):
        log.warning("untagged warning")

    records = [r for r in caplog.records if r.name == "okapipy.generator.test_only"]
    assert len(records) == 1
    assert records[0].getMessage() == "untagged warning"
