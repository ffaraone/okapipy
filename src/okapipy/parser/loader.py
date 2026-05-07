"""Load an OpenAPI 3.x document from a local path or an http(s) URL.

`load_spec` is the public entry point. It auto-detects JSON vs YAML from the file
content, fetches the document (off disk for paths, over HTTP for URLs), and returns
the parsed mapping. `$ref` pointers are deliberately left intact: downstream code
recovers schema names from the original `$ref` strings, and full reference
resolution would be both unnecessary and prohibitively expensive on real-world
specs (deeply self-referential schemas, unreachable external files).

`detect_base_path` reads the path component of the first `servers[].url`, and
`strip_base_path` removes that prefix from each path key so subsequent path-walking
sees segments relative to the API's logical root.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

import yaml

from okapipy.parser.errors import SpecLoadError

log = logging.getLogger(__name__)


def load_spec(source: str | Path) -> dict[str, Any]:
    """Load an OpenAPI 3.x document, preserving `$ref` pointers as-is.

    The source may be a local filesystem path or an http(s) URL. Format (JSON or YAML)
    is auto-detected from the file content.

    Args:
        source: Path or URL pointing to the spec.

    Returns:
        The parsed spec as a plain dict, with `$ref`s left intact.

    Raises:
        SpecLoadError: When the document cannot be located, read, or parsed.
    """
    log.debug("loading OpenAPI spec from %s", source)
    text = _read(source)
    spec = _parse(text, source)
    if not isinstance(spec, dict):
        raise SpecLoadError(f"spec from {source!r} is not a JSON object")
    log.debug("loaded spec contains %d paths", len(spec.get("paths") or {}))
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
            new_key = key[len(base) :] or "/"
            stripped[new_key] = value
        else:
            stripped[key] = value
    return stripped


def _read(source: str | Path) -> str:
    """Read the spec text from a local path or an http(s) URL."""
    text = str(source)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"}:
        try:
            with urlopen(text) as response:  # noqa: S310  # nosec B310 — http(s) is the documented surface
                body: bytes = response.read()
        except OSError as exc:
            raise SpecLoadError(f"failed to load spec from {source!r}: {exc}") from exc
        return body.decode("utf-8")
    path = Path(text).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecLoadError(f"failed to load spec from {source!r}: {exc}") from exc


def _parse(text: str, source: str | Path) -> Any:
    """Parse spec text as JSON first, then fall back to YAML; surface friendly errors."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecLoadError(
            f"spec from {source!r} is not valid JSON or YAML: {exc}"
        ) from exc
