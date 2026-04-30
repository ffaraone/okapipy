"""Shared pytest fixtures and factories for the okapipy test suite."""

from __future__ import annotations

from collections.abc import Iterator  # noqa: TCH003
from pathlib import Path

import pytest
from spacy.language import Language

from okapipy.parser.nlp import clear_pipeline_cache, load_pipeline

FIXTURES_ROOT = Path(__file__).parent / "fixtures"

NLP_CACHE_DIR = Path(__file__).resolve().parent.parent / ".spacy"


@pytest.fixture
def fixtures_dir() -> Path:
    """Filesystem location of the OpenAPI fixture files used across tests."""
    return FIXTURES_ROOT


@pytest.fixture
def simple_spec_path(fixtures_dir: Path) -> Path:
    """Path to a small YAML OpenAPI spec with a single collection and a resource."""
    return fixtures_dir / "simple.yaml"


@pytest.fixture
def simple_spec_json_path(fixtures_dir: Path) -> Path:
    """Path to the JSON variant of the small OpenAPI spec, used for format autodetect."""
    return fixtures_dir / "simple.json"


@pytest.fixture
def external_ref_spec_path(fixtures_dir: Path) -> Path:
    """Path to a spec that references schemas declared in a sibling YAML file."""
    return fixtures_dir / "external_ref.yaml"


@pytest.fixture
def rules_path(fixtures_dir: Path) -> Path:
    """Path to a rules YAML covering both `x-okapipy-ns` and per-op `x-okapipy`."""
    return fixtures_dir / "rules.yaml"


@pytest.fixture
def nested_spec_path(fixtures_dir: Path) -> Path:
    """Path to a multi-level OpenAPI spec used to exercise the builder end to end."""
    return fixtures_dir / "nested.yaml"


@pytest.fixture
def pagination_spec_path(fixtures_dir: Path) -> Path:
    """Path to a spec whose list response uses a custom envelope shape."""
    return fixtures_dir / "pagination.yaml"


@pytest.fixture(scope="session")
def english_nlp() -> Language:
    """A loaded English spaCy pipeline reused across tests for speed."""
    return load_pipeline("en", cache_dir=NLP_CACHE_DIR)


@pytest.fixture(autouse=True)
def _reset_nlp_cache() -> Iterator[None]:
    """Clear the in-process pipeline cache between tests so loader paths are observable.

    The session-scoped `english_nlp` fixture re-populates the cache lazily on use.
    """
    clear_pipeline_cache()
    yield
    clear_pipeline_cache()


@pytest.fixture
def served_fixtures(httpserver: object, fixtures_dir: Path) -> object:
    """Serve the fixtures directory over HTTP for URL-source loader tests.

    The pytest-httpserver `httpserver` fixture is parameterized with one expectation
    per fixture file; tests then build URLs against `httpserver.url_for(...)` to
    exercise the loader's URL branch.
    """
    from pytest_httpserver import HTTPServer  # local import: optional dev dep

    assert isinstance(httpserver, HTTPServer)
    yaml_suffixes = {".yaml", ".yml"}
    for path in fixtures_dir.iterdir():
        if path.is_file():
            content_type = (
                "application/yaml" if path.suffix in yaml_suffixes else "application/json"
            )
            httpserver.expect_request(f"/{path.name}").respond_with_data(
                path.read_bytes(),
                content_type=content_type,
            )
    return httpserver
