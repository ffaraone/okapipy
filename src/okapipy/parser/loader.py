"""Phase 1 of the parser pipeline: load + ref-resolve an OpenAPI document.

A single public entry point, `load_spec`, accepts either a local filesystem path or an
http(s) URL, in JSON or YAML, and returns the fully-resolved document with both
internal and external `$ref` pointers inlined.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from prance import BaseParser, ResolvingParser, ValidationError
from prance.util.url import ResolutionError

from okapipy.parser.errors import SpecLoadError

log = logging.getLogger(__name__)


def load_spec(source: str | Path) -> dict[str, Any]:
    """Load and ref-resolve an OpenAPI 3.x document.

    The source may be a local filesystem path or an http(s) URL. Format (JSON or YAML)
    is auto-detected from the file content. Both internal `#/components/...` references
    and external (relative-file or URL) references are resolved inline before the
    document is returned.

    Args:
        source: Path or URL pointing to the spec.

    Returns:
        The resolved spec as a plain dict.

    Raises:
        SpecLoadError: When the document cannot be located, parsed, or validated, or
            when one of its references cannot be resolved.
    """
    location = _to_prance_url(source)
    log.debug("resolving OpenAPI spec from %s", location)
    try:
        parser = ResolvingParser(
            url=location,
            backend="openapi-spec-validator",
            strict=False,
            lazy=False,
        )
    except (ValidationError, ResolutionError, FileNotFoundError, OSError) as exc:
        raise SpecLoadError(f"failed to load spec from {source!r}: {exc}") from exc
    spec = parser.specification
    if not isinstance(spec, dict):
        raise SpecLoadError(f"resolved spec from {source!r} is not a JSON object")
    log.debug("resolved spec contains %d paths", len(spec.get("paths") or {}))
    return spec


def load_raw_spec(source: str | Path) -> dict[str, Any]:
    """Load an OpenAPI document **without** resolving its `$ref` pointers.

    The resulting dict preserves the original references, which lets downstream code
    recover schema names that would otherwise be lost when prance inlines refs.

    Args:
        source: Path or URL pointing to the spec; same flexibility as `load_spec`.

    Returns:
        The raw spec as a plain dict, refs intact.

    Raises:
        SpecLoadError: When the document cannot be located or parsed.
    """
    location = _to_prance_url(source)
    log.debug("loading raw (unresolved) OpenAPI spec from %s", location)
    try:
        parser = BaseParser(url=location, strict=False, lazy=False)
    except (ValidationError, ResolutionError, FileNotFoundError, OSError) as exc:
        raise SpecLoadError(f"failed to load raw spec from {source!r}: {exc}") from exc
    spec = parser.specification
    if not isinstance(spec, dict):
        raise SpecLoadError(f"raw spec from {source!r} is not a JSON object")
    return spec


def detect_base_path(spec: dict[str, Any]) -> str:
    """Return the path component of the spec's first `servers[].url`, or an empty string.

    OpenAPI 3.x uses `servers` to advertise base URLs; the path portion of the first
    server URL is treated as the API's base path. If no `servers` are declared (or the
    URL has no path), the empty string is returned and no stripping is performed.
    """
    servers = spec.get("servers")
    if not isinstance(servers, list) or not servers:
        return ""
    first = servers[0]
    if not isinstance(first, dict):
        return ""
    url = first.get("url")
    if not isinstance(url, str):
        return ""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path:
        log.debug("detected base path %s from servers[0].url", path)
    return path or ""


def strip_base_path(
    paths: dict[str, dict[str, Any]],
    base: str,
) -> dict[str, dict[str, Any]]:
    """Return a copy of `paths` with `base` removed from each key.

    Keys that do not start with `base` are left unchanged. An empty `base` is a no-op.

    Args:
        paths: The OpenAPI `paths` mapping.
        base: The base prefix to strip (e.g. `/api/v1`).

    Returns:
        A new mapping with the prefix stripped where applicable.
    """
    if not base:
        return dict(paths)
    stripped: dict[str, dict[str, Any]] = {}
    for key, value in paths.items():
        if key.startswith(base):
            new_key = key[len(base):] or "/"
            stripped[new_key] = value
        else:
            stripped[key] = value
    return stripped


def _to_prance_url(source: str | Path) -> str:
    """Normalize a path-or-URL source into the URL form prance accepts."""
    text = str(source)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https", "file"}:
        return text
    return Path(text).expanduser().resolve().as_uri()
