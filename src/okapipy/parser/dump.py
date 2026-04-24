"""Serialize an `APIModel` to JSON or YAML, inferring the format from a path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from okapipy.parser.model import APIModel

JSON_SUFFIXES = {".json"}
YAML_SUFFIXES = {".yaml", ".yml"}


def to_json(api: APIModel) -> str:
    """Return a pretty-printed JSON representation of the APIModel."""
    return api.model_dump_json(indent=2)


def to_yaml(api: APIModel) -> str:
    """Return a YAML representation of the APIModel using JSON-compatible types."""
    data: dict[str, Any] = api.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=False)


def write(api: APIModel, path: Path) -> None:
    """Write the APIModel to `path`, choosing JSON or YAML by file extension.

    Args:
        api: The model to serialize.
        path: Destination file. The extension must be one of `.json`, `.yaml`, `.yml`.

    Raises:
        ValueError: When the file extension is not recognized.
    """
    suffix = path.suffix.lower()
    if suffix in JSON_SUFFIXES:
        path.write_text(to_json(api), encoding="utf-8")
        return
    if suffix in YAML_SUFFIXES:
        path.write_text(to_yaml(api), encoding="utf-8")
        return
    supported = sorted(JSON_SUFFIXES | YAML_SUFFIXES)
    raise ValueError(
        f"unsupported output extension {suffix!r}; expected one of {supported}"
    )
