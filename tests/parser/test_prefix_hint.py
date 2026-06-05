"""Tests for the builder's proactive common-prefix WARNING.

When every path in a spec shares a non-trivial prefix and no
`strip_prefix` is set, the parser logs a single actionable WARNING
suggesting the user add `strip_prefix: <prefix>`. The hint exists
because the typical spec without `servers[]` classifies its leading
segment as an Action and then *every* downstream path raises an
"cannot be attached under Action" error — drowning the real fix in
noise.
"""

from __future__ import annotations

import logging

import pytest
from spacy.language import Language

from okapipy.parser.builder import build
from okapipy.parser.rules import Rules


def _build_spec(paths: list[str]) -> dict[str, object]:
    """Return a minimal OpenAPI document with one trivial GET per path."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1.0"},
        "paths": {
            p: {"get": {"responses": {"200": {"description": "OK"}}}} for p in paths
        },
    }


def test_hint_fires_when_all_paths_share_two_segment_prefix(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """A `/auth/v2/...` spec with no `strip_prefix` surfaces the suggestion."""
    spec = _build_spec(
        ["/auth/v2/tokens", "/auth/v2/users", "/auth/v2/roles", "/auth/v2/sessions"]
    )

    with caplog.at_level(logging.WARNING):
        build(spec, Rules(), english_nlp)

    hints = [r for r in caplog.records if "share the prefix" in r.getMessage()]
    assert len(hints) == 1
    assert "/auth/v2" in hints[0].getMessage()
    assert "strip_prefix: /auth/v2" in hints[0].getMessage()


def test_hint_suppressed_when_strip_prefix_already_set(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """Setting `strip_prefix` explicitly suppresses the suggestion."""
    spec = _build_spec(
        ["/auth/v2/tokens", "/auth/v2/users", "/auth/v2/roles", "/auth/v2/sessions"]
    )

    with caplog.at_level(logging.WARNING):
        build(spec, Rules(), english_nlp, strip_prefix="/auth/v2")

    hints = [r for r in caplog.records if "share the prefix" in r.getMessage()]
    assert hints == []


def test_hint_suppressed_when_servers_provides_a_base_path(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """`detect_base_path` already auto-strips the `servers[].url` prefix."""
    spec = _build_spec(
        ["/auth/v2/tokens", "/auth/v2/users", "/auth/v2/roles", "/auth/v2/sessions"]
    )
    spec["servers"] = [{"url": "https://api.example.com/auth/v2"}]

    with caplog.at_level(logging.WARNING):
        build(spec, Rules(), english_nlp)

    hints = [r for r in caplog.records if "share the prefix" in r.getMessage()]
    assert hints == []


def test_hint_suppressed_when_too_few_paths(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """The hint requires at least four paths to avoid false positives on toy specs."""
    spec = _build_spec(["/auth/v2/tokens", "/auth/v2/users"])

    with caplog.at_level(logging.WARNING):
        build(spec, Rules(), english_nlp)

    hints = [r for r in caplog.records if "share the prefix" in r.getMessage()]
    assert hints == []


def test_hint_suppressed_when_shared_prefix_is_one_segment(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """One shared segment (`/users`) is common, real, and not worth flagging."""
    spec = _build_spec(["/users/a", "/users/b", "/users/c", "/users/d"])

    with caplog.at_level(logging.WARNING):
        build(spec, Rules(), english_nlp)

    hints = [r for r in caplog.records if "share the prefix" in r.getMessage()]
    assert hints == []
