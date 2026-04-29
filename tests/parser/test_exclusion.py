"""Tests for path/operation exclusion via `x-okapipy-exclude` (spec + sidecar)."""

from __future__ import annotations

from pathlib import Path

import pytest
from spacy.language import Language

from okapipy.parser.builder import build
from okapipy.parser.disambiguation import Sidecar, load_sidecar
from okapipy.parser.errors import SidecarFormatError


def test_spec_exclude_star_drops_whole_path(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """`x-okapipy-exclude: '*'` on a path keeps no operations and creates no node."""
    spec = {
        "paths": {
            "/orders": {
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/healthz": {
                "x-okapipy-exclude": "*",
                "get": {"responses": {"200": {"description": "OK"}}},
            },
        }
    }

    with caplog.at_level("INFO"):
        api = build(spec, Sidecar(), english_nlp)

    assert [c.name for c in api.collections] == ["Orders"]
    assert "/healthz" in caplog.text


def test_spec_exclude_method_list_drops_only_those_methods(
    english_nlp: Language,
) -> None:
    """A method list excludes just those verbs; other methods on the path stay."""
    spec = {
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "x-okapipy-exclude": ["DELETE"],
                "get": {"responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"204": {"description": "No content"}}},
            }
        }
    }

    api = build(spec, Sidecar(), english_nlp)

    users = api.collections[0]
    assert users.resource is not None
    assert users.resource.retrieve is not None
    assert users.resource.delete is None


def test_spec_exclude_method_list_is_case_insensitive(english_nlp: Language) -> None:
    """Method names in the exclude list are normalized regardless of casing."""
    spec = {
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "x-okapipy-exclude": ["delete"],
                "get": {"responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"204": {"description": "No content"}}},
            }
        }
    }

    api = build(spec, Sidecar(), english_nlp)

    users = api.collections[0]
    assert users.resource is not None
    assert users.resource.delete is None


def test_sidecar_exclude_star_drops_whole_path(
    english_nlp: Language, tmp_path: Path
) -> None:
    """A sidecar `x-okapipy-exclude: '*'` removes the path even when spec is silent."""
    sidecar_file = tmp_path / "side.yaml"
    sidecar_file.write_text(
        "paths:\n"
        "  /healthz:\n"
        "    x-okapipy-exclude: '*'\n"
    )
    spec = {
        "paths": {
            "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/healthz": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    }

    api = build(spec, load_sidecar(sidecar_file), english_nlp)

    assert [c.name for c in api.collections] == ["Orders"]


def test_sidecar_exclude_method_list_filters_methods(
    english_nlp: Language, tmp_path: Path
) -> None:
    """A sidecar method list filters specific verbs without touching the rest."""
    sidecar_file = tmp_path / "side.yaml"
    sidecar_file.write_text(
        "paths:\n"
        "  /users/{id}:\n"
        "    x-okapipy-exclude: [DELETE]\n"
    )
    spec = {
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "get": {"responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"204": {"description": "No content"}}},
            }
        }
    }

    api = build(spec, load_sidecar(sidecar_file), english_nlp)

    users = api.collections[0]
    assert users.resource is not None
    assert users.resource.retrieve is not None
    assert users.resource.delete is None


def test_sidecar_exclude_overrides_spec_exclude(
    english_nlp: Language, tmp_path: Path
) -> None:
    """When both sidecar and spec declare exclusions for a path, sidecar wins."""
    sidecar_file = tmp_path / "side.yaml"
    sidecar_file.write_text(
        "paths:\n"
        "  /users/{id}:\n"
        "    x-okapipy-exclude: '*'\n"
    )
    spec = {
        "paths": {
            "/users/{id}": {
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "x-okapipy-exclude": ["DELETE"],
                "get": {"responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"204": {"description": "No content"}}},
            }
        }
    }

    api = build(spec, load_sidecar(sidecar_file), english_nlp)

    assert api.collections == []


def test_sidecar_rejects_invalid_exclude_method(tmp_path: Path) -> None:
    """An exclude entry containing a non-HTTP-method string is rejected at load time."""
    sidecar_file = tmp_path / "side.yaml"
    sidecar_file.write_text(
        "paths:\n"
        "  /users/{id}:\n"
        "    x-okapipy-exclude: [BOGUS]\n"
    )

    with pytest.raises(SidecarFormatError, match="BOGUS"):
        load_sidecar(sidecar_file)


def test_sidecar_rejects_non_list_non_star_exclude(tmp_path: Path) -> None:
    """An exclude value that is neither '*' nor a list is rejected at load time."""
    sidecar_file = tmp_path / "side.yaml"
    sidecar_file.write_text(
        "paths:\n"
        "  /users/{id}:\n"
        "    x-okapipy-exclude: 42\n"
    )

    with pytest.raises(SidecarFormatError):
        load_sidecar(sidecar_file)


def test_sidecar_accepts_lowercase_methods(tmp_path: Path) -> None:
    """Lowercase method names are accepted and normalized — case-insensitive."""
    sidecar_file = tmp_path / "side.yaml"
    sidecar_file.write_text(
        "paths:\n"
        "  /users/{id}:\n"
        "    x-okapipy-exclude: [delete, Patch]\n"
    )

    sidecar = load_sidecar(sidecar_file)

    assert sidecar.paths["/users/{id}"].x_okapipy_exclude == ["delete", "Patch"]
