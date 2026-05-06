"""Unit tests for the test-emitter (`okapipy.generator.emit.tests`).

The strategy mirrors the existing emit unit tests: parse a small fixture,
invoke `emit_tests` directly with a freshly built Jinja environment, and
assert on the emitted virtual-FS dict (paths and key snippets in the rendered
content). End-to-end "do the generated tests actually pass" coverage lives
in `test_end_to_end.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from okapipy.generator.emit.tests import emit_tests
from okapipy.generator.templating import make_environment
from okapipy.parser.api import parse

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def project_context() -> dict[str, str]:
    """Project-level Jinja context shared across the templates."""
    return {
        "package": "demoapi",
        "client_class": "DemoClient",
        "project_name": "demoapi-test",
        "project_version": "0.1.0",
        "python_version": "3.13",
        "license": "Proprietary",
    }


def test_emit_tests_writes_conftest_and_client_test(
    project_context: dict[str, str],
) -> None:
    """`emit_tests` always emits `tests/conftest.py` and `tests/test_client.py`."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    assert "tests/conftest.py" in out
    assert "tests/test_client.py" in out
    # conftest pulls the client classes in from the user-layer module.
    assert "from demoapi.client import" in out["tests/conftest.py"]
    assert "DemoClient" in out["tests/conftest.py"]
    assert "AsyncDemoClient" in out["tests/conftest.py"]


def test_emit_tests_one_file_per_collection_and_resource(
    project_context: dict[str, str],
) -> None:
    """A spec with one collection + resource produces one test module per node."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    assert "tests/collections/test_orders.py" in out
    assert "tests/resources/test_order.py" in out
    # Resource subscript access uses the SAMPLE_ID placeholder in the chain.
    assert 'orders["sample-id"]' in out["tests/resources/test_order.py"]


def test_emit_tests_walks_namespaces_and_actions(
    project_context: dict[str, str],
) -> None:
    """A namespace fixture produces namespace + action test modules with chained access."""
    api = parse(FIXTURES / "nested.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    assert "tests/namespaces/test_commerce.py" in out
    assert "tests/actions/test_order_submit.py" in out
    # The collection accessor chain reaches through the namespace.
    assert "client.commerce.orders" in out["tests/collections/test_orders.py"]


def test_emit_tests_skips_create_block_when_collection_has_no_create_op(
    project_context: dict[str, str],
) -> None:
    """The `create()` test is only emitted when the collection's create op exists."""
    api = parse(FIXTURES / "simple.yaml")  # /orders has GET only — no create
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    rendered = out["tests/collections/test_orders.py"]
    assert "iter_yields_items" in rendered
    assert "create_posts_body" not in rendered


def test_emit_tests_uses_pytest_httpx_for_http_mocking(
    project_context: dict[str, str],
) -> None:
    """Emitted tests pull the `httpx_mock: HTTPXMock` fixture from `pytest-httpx`."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    rendered = out["tests/collections/test_orders.py"]
    assert "from pytest_httpx import HTTPXMock" in rendered
    assert "httpx_mock: HTTPXMock" in rendered
    assert "httpx_mock.add_response" in rendered


def test_emit_tests_emits_async_tests_with_pytest_asyncio_marker(
    project_context: dict[str, str],
) -> None:
    """Async tests carry the `@pytest.mark.asyncio` decorator and use `await`."""
    api = parse(FIXTURES / "simple.yaml")
    env = make_environment(None)

    out = emit_tests(env, api, project_context, top_package="demoapi")

    rendered = out["tests/collections/test_orders.py"]
    assert "@pytest.mark.asyncio" in rendered
    assert "async def test_async_" in rendered
    assert "async for" in rendered
