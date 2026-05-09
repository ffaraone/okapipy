"""Shared pytest fixtures and factories for the okapipy test suite."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from spacy.language import Language

from okapipy.generator import generate
from okapipy.generator.vfs import GeneratedFile, write_to_disk
from okapipy.parser.api import parse as parse_spec
from okapipy.parser.model import APIModel
from okapipy.parser.nlp import clear_pipeline_cache, load_pipeline

FIXTURES_ROOT = Path(__file__).parent / "fixtures"

NLP_CACHE_DIR = Path(__file__).resolve().parent.parent / ".spacy"

SIMPLE_FIXTURE = FIXTURES_ROOT / "simple.yaml"
NESTED_FIXTURE = FIXTURES_ROOT / "nested.yaml"

ORDERS_ONLY_SPEC = """
openapi: 3.0.0
info: {title: Sample, version: 1.0.0}
paths:
  /orders:
    get:
      summary: List orders
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Order'}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
"""

ORDERS_AND_PRODUCTS_SPEC = """
openapi: 3.0.0
info: {title: Sample, version: 1.0.0}
paths:
  /orders:
    get:
      summary: List orders
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Order'}
  /products:
    get:
      summary: List products
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Product'}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
    Product: {type: object, properties: {id: {type: string}}}
"""


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


@pytest.fixture
def root_actions_spec_path(fixtures_dir: Path) -> Path:
    """Path to a spec with verb endpoints at the root and under a namespace."""
    return fixtures_dir / "root_actions.yaml"


@pytest.fixture
def singletons_spec_path(fixtures_dir: Path) -> Path:
    """Path to a spec with root, namespace, and resource-level singletons."""
    return fixtures_dir / "singletons.yaml"


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
                "application/yaml"
                if path.suffix in yaml_suffixes
                else "application/json"
            )
            httpserver.expect_request(f"/{path.name}").respond_with_data(
                path.read_bytes(),
                content_type=content_type,
            )
    return httpserver


def _generate_and_import(
    *,
    api: APIModel,
    raw_spec: Path,
    out_dir: Path,
    package: str,
    client_class: str,
    project_name: str,
) -> Iterator[ModuleType]:
    """Generate a project tree, write it to disk, import the `base` subpackage.

    Yields the imported `<package>.base` module, then cleans up `sys.path` and
    `sys.modules` so each test sees a fresh import. Used by the generator
    end-to-end fixtures that exercise the runtime surface of the emitted code.
    """
    vfs = generate(
        api,
        raw_spec=raw_spec,
        output_dir=out_dir,
        package=package,
        client_class=client_class,
        project_name=project_name,
    )
    write_to_disk(vfs, out_dir)
    sys.path.insert(0, str(out_dir / "src"))
    try:
        if package in sys.modules:
            del sys.modules[package]
        module = importlib.import_module(f"{package}.base")
        yield module
    finally:
        sys.path.remove(str(out_dir / "src"))
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


@pytest.fixture
def orders_only_spec_file(tmp_path: Path) -> Path:
    """A tiny OpenAPI spec on disk with a single `/orders` collection."""
    path = tmp_path / "orders_only.yaml"
    path.write_text(ORDERS_ONLY_SPEC, encoding="utf-8")
    return path


@pytest.fixture
def orders_and_products_spec_file(tmp_path: Path) -> Path:
    """The orders-only spec plus a sibling `/products` collection."""
    path = tmp_path / "orders_and_products.yaml"
    path.write_text(ORDERS_AND_PRODUCTS_SPEC, encoding="utf-8")
    return path


@pytest.fixture
def generated_client_module(tmp_path: Path) -> Iterator[ModuleType]:
    """Generate against an empty APIModel, write to disk, import the package.

    The package name (`acmecli`) is fixed; sys.modules cleanup on teardown
    keeps tests independent across parametrizations.
    """
    yield from _generate_and_import(
        api=APIModel(),
        raw_spec=SIMPLE_FIXTURE,
        out_dir=tmp_path / "out",
        package="acmecli",
        client_class="AcmeClient",
        project_name="acme-client",
    )


@pytest.fixture
def client_module(tmp_path: Path) -> Iterator[ModuleType]:
    """Generate against the `simple.yaml` fixture and import the `pagcli` package.

    Tests pull both the client class and runtime types (e.g. pagination
    strategies) off the returned module.
    """
    yield from _generate_and_import(
        api=parse_spec(SIMPLE_FIXTURE),
        raw_spec=SIMPLE_FIXTURE,
        out_dir=tmp_path / "out",
        package="pagcli",
        client_class="PagClient",
        project_name="pag-client",
    )


@pytest.fixture
def async_client_module(tmp_path: Path) -> Iterator[ModuleType]:
    """Generate against `simple.yaml`, write, import — async-tree end-to-end fixture."""
    yield from _generate_and_import(
        api=parse_spec(SIMPLE_FIXTURE),
        raw_spec=SIMPLE_FIXTURE,
        out_dir=tmp_path / "out",
        package="asynccli",
        client_class="AsyncCli",
        project_name="async-cli",
    )


@pytest.fixture
def stubs_vfs(tmp_path: Path) -> dict[str, GeneratedFile]:
    """Generate a tree from the nested fixture and return the in-memory VFS."""
    api = parse_spec(NESTED_FIXTURE)
    return generate(
        api,
        raw_spec=NESTED_FIXTURE,
        output_dir=tmp_path,
        package="acme.client",
        client_class="AcmeClient",
        project_name="acme-client",
    )


@pytest.fixture
def hooks_vfs(tmp_path: Path) -> dict[str, GeneratedFile]:
    """Generate the nested fixture's tree and return the in-memory VFS."""
    api = parse_spec(NESTED_FIXTURE)
    return generate(
        api,
        raw_spec=NESTED_FIXTURE,
        output_dir=tmp_path,
        package="hooks",
        client_class="HooksClient",
        project_name="hooks",
    )


@pytest.fixture
def generated_base(tmp_path: Path) -> Iterator[ModuleType]:
    """Generate the nested fixture, write to disk, import the `base` subpackage."""
    yield from _generate_and_import(
        api=parse_spec(NESTED_FIXTURE),
        raw_spec=NESTED_FIXTURE,
        out_dir=tmp_path / "out",
        package="factorycli",
        client_class="FactoryClient",
        project_name="factory-client",
    )


@pytest.fixture
def manifest_vfs(tmp_path: Path) -> dict[str, GeneratedFile]:
    """Generate a tree from the nested fixture for manifest-shape inspection."""
    api = parse_spec(NESTED_FIXTURE)
    return generate(
        api,
        raw_spec=NESTED_FIXTURE,
        output_dir=tmp_path,
        package="man.client",
        client_class="ManClient",
    )


@pytest.fixture
def project_context() -> dict[str, str]:
    """Project-level Jinja context shared across the test-emitter templates."""
    return {
        "package": "demoapi",
        "client_class": "DemoClient",
        "project_name": "demoapi-test",
        "project_version": "0.1.0",
        "python_version": "3.13",
        "license": "Proprietary",
    }
