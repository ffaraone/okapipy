"""Public entry point of the okapipy structural parser."""

from __future__ import annotations

from pathlib import Path

from okapipy.parser.builder import build
from okapipy.parser.inline_schemas import flatten_inline_schemas
from okapipy.parser.loader import load_spec
from okapipy.parser.model import APIModel
from okapipy.parser.nlp import DEFAULT_CACHE_DIR, load_pipeline
from okapipy.parser.rules import load_rules

DEFAULT_NLP_CACHE_DIR = DEFAULT_CACHE_DIR


def parse(
    source: str | Path,
    rules: str | Path | None = None,
    lang: str = "en",
    *,
    strip_prefix: str | None = None,
    nlp_cache_dir: Path = DEFAULT_NLP_CACHE_DIR,
    unmatched_namespace: str | None = None,
) -> APIModel:
    """Parse an OpenAPI 3.x document into an APIModel tree.

    Args:
        source: A local filesystem path or http(s) URL pointing to a JSON or YAML
            OpenAPI document. Format is auto-detected by content.
        rules: Optional local path to a JSON/YAML rules file that mirrors the
            OpenAPI extension shape (root `x-okapipy-ns` and per-operation
            `x-okapipy-kind`). URLs are not accepted.
        lang: ISO language code controlling which spaCy model is loaded.
        strip_prefix: Optional path prefix to strip from every path before
            classification, e.g. `/public/v1`. When set, overrides the prefix
            inferred from `servers[].url`.
        nlp_cache_dir: Directory under which spaCy models are stored and looked up.
            On a cache miss the model is downloaded into this directory.
        unmatched_namespace: When set, operations that would otherwise be
            dropped by the routing table are retained as synthetic actions
            under a top-level namespace of this name. Raises
            `UnmatchedNamespaceCollisionError` if the name collides with
            an existing top-level node.

    Returns:
        The fully-built APIModel rooted at the namespaces it discovered.
    """
    spec = load_spec(source)
    spec = flatten_inline_schemas(spec)
    loaded_rules = load_rules(rules)
    nlp = load_pipeline(lang, cache_dir=nlp_cache_dir)
    return build(
        spec,
        loaded_rules,
        nlp,
        strip_prefix=strip_prefix,
        unmatched_namespace=unmatched_namespace,
    )
