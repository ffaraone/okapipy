"""Tests for the spaCy loader, segment analyzer, and CLI fetch hook."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from okapipy.parser import nlp as nlp_mod
from okapipy.parser.errors import NlpModelMissingError
from okapipy.parser.nlp import (
    LANG_TO_MODEL,
    SegmentInfo,
    analyze_segment,
    fetch_model,
    load_pipeline,
    model_name_for,
    model_path,
)


def test_model_name_for_known_language_returns_spacy_package() -> None:
    """English maps to en_core_web_sm; the table is the source of truth."""
    assert model_name_for("en") == LANG_TO_MODEL["en"]


def test_model_name_for_unknown_language_raises_missing() -> None:
    """A code not in the table is rejected up-front, not deferred to download time."""
    with pytest.raises(NlpModelMissingError, match="language 'xx'"):
        model_name_for("xx")


def test_model_path_returns_package_root_when_no_versioned_subdir(
    tmp_path: Path,
) -> None:
    """With no versioned subdirectory yet, `model_path` returns the package root."""
    assert model_path("en", tmp_path) == tmp_path / "en_core_web_sm"


def test_model_path_resolves_versioned_subdir_when_present(tmp_path: Path) -> None:
    """A versioned subdirectory under the package root is preferred when present."""
    package = tmp_path / "en_core_web_sm"
    versioned = package / "en_core_web_sm-3.8.0"
    versioned.mkdir(parents=True)

    assert model_path("en", tmp_path) == versioned


def test_load_pipeline_uses_cached_model_without_download(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """When the model package already exists, no download is attempted."""
    (tmp_path / "en_core_web_sm").mkdir()
    fake_pipeline = mocker.Mock(name="Language")
    spacy_load = mocker.patch(
        "okapipy.parser.nlp.spacy.load", return_value=fake_pipeline
    )
    download = mocker.patch("okapipy.parser.nlp.fetch_model")

    result = load_pipeline("en", cache_dir=tmp_path)

    assert result is fake_pipeline
    spacy_load.assert_called_once()
    download.assert_not_called()


def test_load_pipeline_downloads_when_missing(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A cache miss triggers fetch_model exactly once, then loads the result."""
    fake_pipeline = mocker.Mock(name="Language")
    download = mocker.patch(
        "okapipy.parser.nlp.fetch_model",
        side_effect=lambda lang, cache_dir: (cache_dir / "en_core_web_sm").mkdir(),
    )
    mocker.patch("okapipy.parser.nlp.spacy.load", return_value=fake_pipeline)

    load_pipeline("en", cache_dir=tmp_path)

    download.assert_called_once_with("en", tmp_path)


def test_load_pipeline_caches_pipeline_per_lang_and_dir(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """Repeat calls with the same args reuse the in-process pipeline."""
    (tmp_path / "en_core_web_sm").mkdir()
    fake_pipeline = mocker.Mock(name="Language")
    spacy_load = mocker.patch(
        "okapipy.parser.nlp.spacy.load", return_value=fake_pipeline
    )

    load_pipeline("en", cache_dir=tmp_path)
    load_pipeline("en", cache_dir=tmp_path)

    assert spacy_load.call_count == 1


def test_fetch_model_invokes_spacy_download_with_target(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """fetch_model shells out to `python -m spacy download <model> --target <dir>`."""
    run = mocker.patch("okapipy.parser.nlp.subprocess.run")

    fetch_model("en", cache_dir=tmp_path)

    args = run.call_args.args[0]
    assert args[1:4] == ["-m", "spacy", "download"]
    assert "en_core_web_sm" in args
    assert "--target" in args
    assert str(tmp_path) in args


def test_fetch_model_wraps_subprocess_failure(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """A non-zero exit from spacy download surfaces as NlpModelMissingError."""
    mocker.patch(
        "okapipy.parser.nlp.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["spacy"]),
    )

    with pytest.raises(NlpModelMissingError, match="language 'en'"):
        fetch_model("en", cache_dir=tmp_path)


def test_analyze_segment_detects_verb_phrase(mocker: MockerFixture) -> None:
    """A segment containing any verb token is marked as a verb-phrase."""
    nlp = mocker.Mock(name="Language")
    mocker.patch.object(
        nlp_mod,
        "_analyze_token",
        side_effect=[(False, False), (True, False)],
    )

    info = analyze_segment(nlp, "reset-password")

    assert info == SegmentInfo("reset-password", True, False, False)


def test_analyze_segment_detects_plural_noun(mocker: MockerFixture) -> None:
    """A segment whose only signal is a plural noun is classified as plural."""
    nlp = mocker.Mock(name="Language")
    mocker.patch.object(nlp_mod, "_analyze_token", return_value=(False, True))

    info = analyze_segment(nlp, "orders")

    assert info.is_plural is True
    assert info.is_verb_phrase is False


def test_analyze_segment_falls_back_to_singular_or_unknown(
    mocker: MockerFixture,
) -> None:
    """Tokens without a verb or plural signal yield the singular-or-unknown bucket."""
    nlp = mocker.Mock(name="Language")
    mocker.patch.object(nlp_mod, "_analyze_token", return_value=(False, False))

    info = analyze_segment(nlp, "commerce")

    assert info.is_singular_or_unknown is True


def test_analyze_segment_detects_plural_for_words_spacy_mistags_in_isolation(
    english_nlp: object,
) -> None:
    """Words like `tokens` get tagged PROPN-Sing in isolation; the wrapper recovers Plur.

    This is the regression test for the `/auth/tokens` failure: without the wrapper,
    `tokens` was classified as a namespace and POST was rejected at the leaf.
    """
    from spacy.language import Language

    assert isinstance(english_nlp, Language)

    info = analyze_segment(english_nlp, "tokens")

    assert info.is_plural is True


def test_analyze_segment_keeps_verb_signal_for_action_verbs(
    english_nlp: object,
) -> None:
    """A standalone verb like `reset` is still recognized as a verb-phrase."""
    from spacy.language import Language

    assert isinstance(english_nlp, Language)

    info = analyze_segment(english_nlp, "reset")

    assert info.is_verb_phrase is True


def test_analyze_segment_treats_compound_with_singular_head_as_verb_phrase(
    english_nlp: object,
) -> None:
    """`force-reimport` reads as a verb-phrase: a compound whose head isn't plural.

    Without this rule the bare-token POS for both `force` and `reimport` is NOUN, so
    the classifier would otherwise route the segment to a sub-collection.
    """
    from spacy.language import Language

    assert isinstance(english_nlp, Language)

    info = analyze_segment(english_nlp, "force-reimport")

    assert info.is_verb_phrase is True


def test_analyze_segment_keeps_plural_compound_as_collection(
    english_nlp: object,
) -> None:
    """`password-recovery-requests` stays a plural collection — its head is plural."""
    from spacy.language import Language

    assert isinstance(english_nlp, Language)

    info = analyze_segment(english_nlp, "password-recovery-requests")

    assert info.is_plural is True
    assert info.is_verb_phrase is False


@pytest.mark.parametrize(
    "verb",
    ["login", "logout", "refresh", "ping", "subscribe", "verify"],
)
def test_analyze_segment_uses_english_verb_action_registry(
    english_nlp: object, verb: str
) -> None:
    """Common API verbs that the small spaCy model mistags as nouns are caught.

    `en_core_web_sm` returns `NOUN`/`PROPN` for tokens like `login` and `refresh`;
    the language-specific verb-action registry restores the verb signal so root
    paths like `/login` classify as actions without an explicit `x-okapipy-kind`.
    """
    from spacy.language import Language

    assert isinstance(english_nlp, Language)

    info = analyze_segment(english_nlp, verb)

    assert info.is_verb_phrase is True


def test_verb_action_registry_is_language_scoped(mocker: MockerFixture) -> None:
    """A pipeline whose `lang` has no registry entry falls back to spaCy alone.

    Other languages currently rely on spaCy verb tagging or `x-okapipy-kind:
    action`; the English-only registry must not bleed into them.
    """
    nlp = mocker.Mock(name="Language")
    nlp.lang = "fr"
    bare_token = mocker.Mock()
    bare_token.pos_ = "NOUN"
    bare_token.morph.get.return_value = []
    bare_doc = mocker.MagicMock()
    bare_doc.__len__.return_value = 1
    bare_doc.__getitem__.return_value = bare_token
    nlp.return_value = bare_doc

    from okapipy.parser.nlp import _is_registered_verb

    assert _is_registered_verb(nlp, "login") is False
