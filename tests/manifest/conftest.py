"""Shared fixtures for project-manifest tests.

Manifest tests exercise IO + schema validation, so each test writes a
small YAML/JSON file under `tmp_path` and points `load_manifest` at it.
`write_manifest` is the factory used to assemble that file.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def write_manifest(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a manifest file under `tmp_path`.

    The factory accepts arbitrary keyword arguments which are merged onto a
    minimal-valid manifest (`package`, `client_class`, and a single root-mount
    spec) and serialized to YAML. Callers can pass `format="json"` to switch
    encodings, or override `filename` to control the on-disk name. Returns
    the absolute path of the written file.
    """

    def _factory(
        *,
        filename: str = "okapipy.yml",
        format: str = "yaml",
        overrides: Mapping[str, Any] | None = None,
        spec_overrides: Mapping[str, Any] | None = None,
    ) -> Path:
        spec: dict[str, Any] = {
            "namespace": "",
            "source": "specs/example.yaml",
        }
        if spec_overrides is not None:
            spec.update(spec_overrides)
        payload: dict[str, Any] = {
            "package": "acme.commerce",
            "client_class": "CommerceClient",
            "specs": [spec],
        }
        if overrides is not None:
            payload.update(overrides)
        path = tmp_path / filename
        if format == "json":
            import json

            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    return _factory
