"""Unit tests for the test-emitter (`okapipy.generator.emit.tests`).

The strategy mirrors the existing emit unit tests: parse a small fixture,
invoke `emit_tests` directly with a freshly built Jinja environment, and
assert on the emitted virtual-FS dict (paths and key snippets in the rendered
content). End-to-end "do the generated tests actually pass" coverage lives
in `test_end_to_end.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from okapipy.generator.emit.tests import emit_tests
from okapipy.generator.templating import make_environment
from okapipy.parser.api import parse

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_emit_tests_writes_conftest_and_client_test(
    project_context: dict[str, Any],
) -> None:
    """`emit_tests` always emits a `conftest.py` + `test_client.py` under the package path."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    assert "tests/demoapi/conftest.py" in out
    assert "tests/demoapi/test_client.py" in out
    # conftest pulls the client classes in from the user-layer module.
    assert "from demoapi.client import" in out["tests/demoapi/conftest.py"]
    assert "DemoClient" in out["tests/demoapi/conftest.py"]
    assert "AsyncDemoClient" in out["tests/demoapi/conftest.py"]


def test_emit_tests_emits_init_markers_along_package_path(
    project_context: dict[str, Any],
) -> None:
    """Empty `__init__.py` markers exist at each level so pytest's default importer
    can disambiguate same-named test modules that live under different flavors of the
    client (e.g. `acme.commerce.models` and `acme.commerce.dicts`).
    """
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)
    ctx = {**project_context, "package": "acme.commerce.models"}

    out = emit_tests(env, api, ctx, top_package="acme")

    assert "tests/__init__.py" in out
    assert "tests/acme/__init__.py" in out
    assert "tests/acme/commerce/__init__.py" in out
    assert "tests/acme/commerce/models/__init__.py" in out
    # All markers are empty placeholders.
    for path, content in out.items():
        if path.endswith("/__init__.py"):
            assert content == ""


def test_emit_tests_one_file_per_collection_and_resource(
    project_context: dict[str, Any],
) -> None:
    """A spec with one collection + resource produces one test module per node."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    assert "tests/demoapi/collections/test_orders.py" in out
    assert "tests/demoapi/resources/test_order.py" in out
    # Resource subscript access uses the SAMPLE_ID placeholder in the chain.
    assert 'orders["sample-id"]' in out["tests/demoapi/resources/test_order.py"]


def test_emit_tests_walks_namespaces_and_actions(
    project_context: dict[str, Any],
) -> None:
    """A namespace fixture produces namespace + action test modules with chained access."""
    api = parse(FIXTURES / "nested.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    assert "tests/demoapi/namespaces/test_commerce.py" in out
    assert "tests/demoapi/actions/test_order_submit.py" in out
    # The collection accessor chain reaches through the namespace.
    assert "client.commerce.orders" in out["tests/demoapi/collections/test_orders.py"]


def test_emit_tests_skips_create_block_when_collection_has_no_create_op(
    project_context: dict[str, Any],
) -> None:
    """The `create()` test is only emitted when the collection's create op exists."""
    api = parse(FIXTURES / "simple.yaml")  # /orders has GET only — no create
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    rendered = out["tests/demoapi/collections/test_orders.py"]
    assert "iter_yields_items" in rendered
    assert "create_posts_body" not in rendered


def test_emit_tests_uses_pytest_httpx_for_http_mocking(
    project_context: dict[str, Any],
) -> None:
    """Emitted tests pull the `httpx_mock: HTTPXMock` fixture from `pytest-httpx`."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    rendered = out["tests/demoapi/collections/test_orders.py"]
    assert "from pytest_httpx import HTTPXMock" in rendered
    assert "httpx_mock: HTTPXMock" in rendered
    assert "httpx_mock.add_response" in rendered


def test_emit_tests_emits_async_tests_with_pytest_asyncio_marker(
    project_context: dict[str, Any],
) -> None:
    """Async tests carry the `@pytest.mark.asyncio` decorator and use `await`."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    rendered = out["tests/demoapi/collections/test_orders.py"]
    assert "@pytest.mark.asyncio" in rendered
    assert "async def test_async_" in rendered
    assert "async for" in rendered
