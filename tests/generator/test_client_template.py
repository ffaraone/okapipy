"""Tests for the templated sync/async client classes.

The strategy: generate a tree against an empty APIModel into a `tmp_path`, import
the resulting client module, and exercise its surface against `httpx.MockTransport`.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.model import APIModel


@pytest.fixture
def generated_client_module(tmp_path: Path):
    """Generate a tree, write to disk, import the runtime + client module.

    The package name is unique per test (`acmecli_<n>`) so multiple parametrized
    test runs don't collide in `sys.modules`. Returns the imported package module.
    """
    package = "acmecli"
    out = tmp_path / "out"
    vfs = generate(
        APIModel(),
        raw_spec=Path("tests/fixtures/simple.yaml"),
        output_dir=out,
        package=package,
        client_class="AcmeClient",
        project_name="acme-client",
        base_url_default="https://api.example.com",
    )
    write_to_disk(vfs, out)
    sys.path.insert(0, str(out / "src"))
    try:
        if package in sys.modules:
            del sys.modules[package]
        module = importlib.import_module(package)
        yield module
    finally:
        sys.path.remove(str(out / "src"))
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


def test_client_class_is_constructable(generated_client_module) -> None:
    """`AcmeClient(...)` instantiates with default base_url and exposes a shape."""
    client = generated_client_module.AcmeClient()

    assert client.base_url == "https://api.example.com"
    assert client.shape == "models"
    client.close()


def test_client_uses_explicit_base_url(generated_client_module) -> None:
    """An explicit `base_url=...` overrides the baked-in default."""
    client = generated_client_module.AcmeClient(base_url="https://other.example.com")

    assert client.base_url == "https://other.example.com"
    client.close()


def test_with_shape_returns_sibling_sharing_transport(generated_client_module) -> None:
    """`with_shape("dicts")` returns a sibling pointing at the same `_http`."""
    client = generated_client_module.AcmeClient()
    sibling = client.with_shape("dicts")

    assert sibling is not client
    assert sibling.shape == "dicts"
    assert sibling._http is client._http
    assert sibling.base_url == client.base_url
    client.close()


def test_from_response_branches_on_shape(generated_client_module) -> None:
    """`from_response` validates with Pydantic in `models` shape, returns dict in `dicts`."""

    class _M:
        @classmethod
        def model_validate(cls, raw):
            return ("validated", raw)

    client_models = generated_client_module.AcmeClient(shape="models")
    client_dicts = client_models.with_shape("dicts")

    assert client_models.from_response(_M, {"id": 1}) == ("validated", {"id": 1})
    assert client_dicts.from_response(_M, {"id": 1}) == {"id": 1}
    assert client_models.from_response(None, {"id": 1}) == {"id": 1}
    client_models.close()


def test_client_forwards_auth_and_headers(generated_client_module) -> None:
    """`auth` and `headers` are forwarded verbatim to the underlying `httpx.Client`."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"ok": True})
    )
    client = generated_client_module.AcmeClient(
        transport=transport,
        auth=httpx.BasicAuth("u", "p"),
        headers={"X-Custom": "value"},
    )
    response = client._http.get("/orders")

    assert response.status_code == 200
    # Header passed through to the request the mock saw:
    request = response.request
    assert request.headers["X-Custom"] == "value"
    assert request.headers["Authorization"].startswith("Basic ")
    client.close()


def test_retries_wrap_user_transport(generated_client_module) -> None:
    """`retries=RetryPolicy(...)` wraps the user-supplied transport in `RetryTransport`."""
    runtime = importlib.import_module("acmecli.transport")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = generated_client_module.AcmeClient(
        transport=transport,
        retries=runtime.RetryPolicy(total=3, backoff=0.0),
    )
    response = client._http.get("/orders")

    assert response.status_code == 200
    assert calls["n"] == 3  # two retries + the success
    client.close()


def test_post_is_never_retried_through_client(generated_client_module) -> None:
    """The GET-only retry rule applies through the templated client too."""
    runtime = importlib.import_module("acmecli.transport")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    client = generated_client_module.AcmeClient(
        transport=transport,
        retries=runtime.RetryPolicy(total=5, backoff=0.0),
    )
    response = client._http.post("/orders", json={})

    assert response.status_code == 503
    assert calls["n"] == 1
    client.close()


def test_async_client_constructable(generated_client_module) -> None:
    """The async sibling instantiates and exposes the same shape API."""
    client = generated_client_module.AsyncAcmeClient()

    assert client.base_url == "https://api.example.com"
    assert client.shape == "models"
    sibling = client.with_shape("dicts")
    assert sibling.shape == "dicts"
    assert sibling._http is client._http
