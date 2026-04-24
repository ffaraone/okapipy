"""Phase 2 of the parser pipeline: spaCy-backed POS and morphology lookup.

This module owns three responsibilities:

1. Mapping ISO language codes to spaCy model names.
2. Loading a spaCy pipeline from a user-controlled cache directory, downloading the
   model on a cache miss.
3. Reducing a path segment (which may contain dashes or underscores) to a small
   summary the classifier can branch on: is it a verb-phrase, is it plural, etc.
"""

from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

import spacy
from spacy.language import Language

from okapipy.parser.errors import NlpModelMissingError

DEFAULT_CACHE_DIR = Path.cwd() / ".spacy"

LANG_TO_MODEL: dict[str, str] = {
    "en": "en_core_web_sm",
    "es": "es_core_news_sm",
    "fr": "fr_core_news_sm",
    "de": "de_core_news_sm",
    "it": "it_core_news_sm",
    "pt": "pt_core_news_sm",
    "nl": "nl_core_news_sm",
}

# Sentence-context wrappers that force spaCy to produce noun morphology with the right
# Number feature. Bare tokens are tagged as PROPN with Number=Sing by the small models,
# which would otherwise misclassify plural collection segments like `tokens` or `users`.
PLURAL_CONTEXT: dict[str, str] = {
    "en": "the {}",
    "es": "los {}",
    "fr": "les {}",
    "de": "die {}",
    "it": "i {}",
    "pt": "os {}",
    "nl": "de {}",
}

_PIPELINE_CACHE: dict[tuple[str, str], Language] = {}

_TOKEN_SPLIT = re.compile(r"[-_]+")


class SegmentInfo(NamedTuple):
    """The classifier-facing summary of a single path segment.

    Attributes:
        text: The original segment as it appeared in the OpenAPI path.
        is_verb_phrase: True when at least one token in the segment is tagged as a verb.
        is_plural: True when at least one token is a plural noun, and no token is a verb.
        is_singular_or_unknown: True when the segment is neither a verb-phrase nor plural.
    """

    text: str
    is_verb_phrase: bool
    is_plural: bool
    is_singular_or_unknown: bool


def model_name_for(lang: str) -> str:
    """Return the spaCy model name for an ISO language code.

    Raises:
        NlpModelMissingError: When the language has no entry in the model table.
    """
    try:
        return LANG_TO_MODEL[lang]
    except KeyError as exc:
        raise NlpModelMissingError(lang, str(DEFAULT_CACHE_DIR)) from exc


def model_path(lang: str, cache_dir: Path) -> Path:
    """Return the on-disk directory that holds the spaCy model for `lang`.

    `python -m spacy download --target` installs the model as a Python package laid
    out as `<cache_dir>/<package>/<package>-<version>/...`. This helper resolves the
    versioned subdirectory when one exists, and otherwise returns the package root
    (which is what tests using a stub directory will see).
    """
    package_root = cache_dir / model_name_for(lang)
    if not package_root.exists():
        return package_root
    prefix = package_root.name
    inner = sorted(
        p for p in package_root.iterdir() if p.is_dir() and p.name.startswith(prefix)
    )
    return inner[0] if inner else package_root


def load_pipeline(lang: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Language:
    """Load the spaCy pipeline for `lang`, downloading it on a cache miss.

    The pipeline is cached per-process keyed by `(lang, cache_dir)`, so repeated calls
    are cheap. On a cache miss the model is downloaded into `cache_dir` using
    `python -m spacy download <model> --target <cache_dir>`. Subsequent calls reuse
    the on-disk copy without touching the network.

    Args:
        lang: ISO language code; must exist in the language-to-model table.
        cache_dir: Directory under which model packages live.

    Returns:
        A loaded spaCy `Language` pipeline ready for tagging.

    Raises:
        NlpModelMissingError: When the language is unknown or the download fails.
    """
    key = (lang, str(cache_dir.resolve()))
    cached = _PIPELINE_CACHE.get(key)
    if cached is not None:
        return cached
    package_root = cache_dir / model_name_for(lang)
    if not package_root.exists():
        fetch_model(lang, cache_dir)
    target = model_path(lang, cache_dir)
    pipeline = spacy.load(str(target))
    _PIPELINE_CACHE[key] = pipeline
    return pipeline


def fetch_model(lang: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    """Download the spaCy model for `lang` into `cache_dir` and return its path.

    Uses spaCy's own `download` command, passing `--target` so the package is laid
    out under `cache_dir/<model_name>/...` instead of being installed globally.

    Raises:
        NlpModelMissingError: When the download fails for any reason (network down,
            unknown model name, pip failure).
    """
    name = model_name_for(lang)
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "spacy",
                "download",
                name,
                "--target",
                str(cache_dir),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise NlpModelMissingError(lang, str(cache_dir)) from exc
    return cache_dir / name


def analyze_segment(nlp: Language, segment: str) -> SegmentInfo:
    """Reduce a raw path segment to the booleans the classifier needs.

    The segment is split on `-` and `_`, each token is independently POS-tagged, and
    the results are combined:

    1. A clear verb anywhere in the segment wins (e.g. `reset` in `reset-password`).
    2. A plural noun wins next (e.g. `requests` in `password-recovery-requests`).
    3. A multi-word segment whose head is not plural is treated as a verb-phrase
       action (e.g. `force-reimport`, `send-email`) — compound nouns in REST paths
       almost always end with a plural head, so non-plural compounds signal an action.
    4. Otherwise the segment is reported as singular-or-unknown.

    Args:
        nlp: A loaded spaCy pipeline.
        segment: The original segment string (e.g. `reset-password`).

    Returns:
        A `SegmentInfo` with three mutually exclusive flags set.
    """
    tokens = [t for t in _TOKEN_SPLIT.split(segment) if t]
    is_verb = False
    is_plural = False
    for token in tokens:
        verb, plural = _analyze_token(nlp, token)
        if verb:
            is_verb = True
            break
        if plural:
            is_plural = True
    if is_verb:
        return SegmentInfo(segment, True, False, False)
    if is_plural:
        return SegmentInfo(segment, False, True, False)
    if len(tokens) > 1:
        _, last_is_plural = _analyze_token(nlp, tokens[-1])
        if not last_is_plural:
            return SegmentInfo(segment, True, False, False)
    return SegmentInfo(segment, False, False, True)


@lru_cache(maxsize=4096)
def _analyze_token(nlp: Language, token: str) -> tuple[bool, bool]:
    """Return `(is_verb, is_plural)` for a single bare token, memoized per pipeline.

    Uses two analyses to work around small spaCy models tagging bare tokens as PROPN
    with `Number=Sing`: the **bare** form gives a reliable VERB signal (verbs like
    `reset` or `submit` are infinitives in isolation), while a **definite-article
    context** like `"the tokens"` gives a reliable plural signal.
    """
    bare_doc = nlp(token)
    if not len(bare_doc):
        return False, False
    is_verb = bare_doc[0].pos_ == "VERB"
    is_plural = _detect_plural(nlp, token, fallback=bare_doc[0])
    return is_verb, is_plural


def _detect_plural(nlp: Language, token: str, fallback: Any) -> bool:
    """Return True when `token` is a plural noun, using a language-specific wrapper."""
    target = _wrapped_token(nlp, token, fallback=fallback)
    if target is None:
        return False
    number = target.morph.get("Number", default=[])
    return "Plur" in number


def lemma_in_context(nlp: Language, token: str) -> str:
    """Return the noun lemma of `token` using the language's definite-article wrapper.

    Lemmatization in isolation is unreliable: small spaCy models tag bare unknown words
    as `PROPN`, which leaves the surface form unchanged. Wrapping the token in a
    determiner (e.g. `the tokens`) coaxes the tagger into a noun analysis and yields a
    proper singular lemma.
    """
    target = _wrapped_token(nlp, token, fallback=None)
    if target is None:
        return token
    return target.lemma_ or token


def _wrapped_token(nlp: Language, token: str, *, fallback: Any) -> Any:
    """Return the spaCy token analyzed under the language wrapper, or `fallback`."""
    lang = nlp.lang or ""
    template = PLURAL_CONTEXT.get(lang)
    if template is None:
        return fallback
    doc = nlp(template.format(token))
    return next((t for t in doc if t.text == token), fallback)


def clear_pipeline_cache() -> None:
    """Drop all cached pipelines; primarily used by tests."""
    _PIPELINE_CACHE.clear()
    _analyze_token.cache_clear()
