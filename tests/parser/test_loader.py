"""Tests for the spec loader: file, URL, JSON, YAML, internal and external refs."""

from __future__ import annotations

from pathlib import Path

import pytest

from okapipy.parser.errors import SpecLoadError
from okapipy.parser.loader import detect_base_path, load_spec, strip_base_path


def test_load_spec_preserves_internal_refs(simple_spec_path: Path) -> None:
    """A YAML file with internal $refs is loaded and the refs are kept intact."""
    spec = load_spec(simple_spec_path)

    list_op = spec["paths"]["/orders"]["get"]
    schema = list_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/OrderList"}


def test_load_spec_json_file_is_autodetected(simple_spec_json_path: Path) -> None:
    """The JSON variant of a spec is detected and parsed without an explicit hint."""
    spec = load_spec(simple_spec_json_path)

    assert spec["openapi"] == "3.0.0"
    assert "/orders" in spec["paths"]


def test_load_spec_preserves_external_refs(external_ref_spec_path: Path) -> None:
    """Refs pointing at sibling files are kept verbatim — resolution is deferred."""
    spec = load_spec(external_ref_spec_path)

    schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "$ref" in schema


def test_load_spec_accepts_url_source(served_fixtures: object) -> None:
    """The loader treats http(s) sources the same as filesystem paths."""
    from pytest_httpserver import HTTPServer

    assert isinstance(served_fixtures, HTTPServer)
    url = served_fixtures.url_for("/simple.yaml")

    spec = load_spec(url)

    assert spec["info"]["title"] == "Simple API"


def test_load_spec_missing_file_raises_spec_load_error(tmp_path: Path) -> None:
    """A non-existent path is wrapped as a SpecLoadError with the source mentioned."""
    missing = tmp_path / "does-not-exist.yaml"

    with pytest.raises(SpecLoadError, match="does-not-exist"):
        load_spec(missing)


def test_detect_base_path_uses_first_server_url_path() -> None:
    """The path portion of the first `servers[].url` is treated as the base path."""
    spec = {"servers": [{"url": "https://api.example.com/api/v1"}]}

    assert detect_base_path(spec) == "/api/v1"


def test_detect_base_path_strips_trailing_slash() -> None:
    """A trailing slash on the server URL path is removed."""
    spec = {"servers": [{"url": "https://example.com/api/v1/"}]}

    assert detect_base_path(spec) == "/api/v1"


def test_detect_base_path_returns_empty_when_servers_missing() -> None:
    """A spec without `servers` declared yields an empty base path."""
    assert detect_base_path({"paths": {}}) == ""


def test_detect_base_path_returns_empty_when_url_has_no_path() -> None:
    """A server URL pointing at a bare host has no path component to strip."""
    spec = {"servers": [{"url": "https://api.example.com"}]}

    assert detect_base_path(spec) == ""


def test_strip_base_path_removes_prefix_from_keys() -> None:
    """Each key prefixed with `base` loses the prefix; others are kept untouched."""
    paths = {"/api/v1/orders": {"x": 1}, "/api/v1/orders/{id}": {"y": 2}}

    stripped = strip_base_path(paths, "/api/v1")

    assert stripped == {"/orders": {"x": 1}, "/orders/{id}": {"y": 2}}


def test_strip_base_path_root_when_path_equals_base() -> None:
    """A path identical to `base` becomes `/`, not the empty string."""
    paths = {"/api/v1": {"x": 1}}

    assert strip_base_path(paths, "/api/v1") == {"/": {"x": 1}}


def test_strip_base_path_no_op_when_base_empty() -> None:
    """An empty base returns a copy of the input mapping unchanged."""
    paths = {"/orders": {"x": 1}}

    assert strip_base_path(paths, "") == paths


def test_strip_base_path_preserves_paths_outside_prefix() -> None:
    """Paths that don't start with the prefix are kept verbatim."""
    paths = {"/api/v1/orders": {"x": 1}, "/healthz": {"y": 2}}

    assert strip_base_path(paths, "/api/v1") == {
        "/orders": {"x": 1},
        "/healthz": {"y": 2},
    }


def test_detect_base_path_returns_empty_when_servers_not_a_list() -> None:
    """A `servers` field of the wrong type is treated as if missing."""
    assert detect_base_path({"servers": "https://example.com"}) == ""


def test_detect_base_path_returns_empty_when_first_server_is_not_dict() -> None:
    """The first server entry must be a dict; otherwise no base path is derived."""
    assert detect_base_path({"servers": ["https://example.com"]}) == ""


def test_detect_base_path_returns_empty_when_url_is_not_string() -> None:
    """A non-string `url` field is rejected the same as a missing one."""
    assert detect_base_path({"servers": [{"url": 42}]}) == ""
