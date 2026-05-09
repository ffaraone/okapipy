"""Tests for the templated sync/async client classes.

The strategy: generate a tree against an empty APIModel into a `tmp_path`, import
the resulting client module, and exercise its surface against `httpx.MockTransport`.
"""

from __future__ import annotations

import importlib

import httpx
import pytest


def test_client_class_is_constructable(generated_client_module) -> None:
    """`AcmeClient(base_url=...)` instantiates and exposes shape + base_url."""
    client = generated_client_module.AcmeClientBase("https://api.example.com")

    assert client.base_url == "https://api.example.com"
    assert client.shape == "models"
    client.close()


def test_client_requires_base_url(generated_client_module) -> None:
    """`base_url` is a required positional argument; constructing without it raises."""
    with pytest.raises(TypeError, match="base_url"):
        generated_client_module.AcmeClientBase()


def test_with_shape_returns_sibling_sharing_transport(generated_client_module) -> None:
    """`with_shape("dicts")` returns a sibling pointing at the same `_http`."""
    client = generated_client_module.AcmeClientBase("https://api.example.com")
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

    client_models = generated_client_module.AcmeClientBase(
        "https://api.example.com", shape="models"
    )
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
    client = generated_client_module.AcmeClientBase(
        "https://api.example.com",
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
    runtime = importlib.import_module("acmecli.base.transport")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = generated_client_module.AcmeClientBase(
        "https://api.example.com",
        transport=transport,
        retries=runtime.RetryPolicy(total=3, backoff=0.0),
    )
    response = client._http.get("/orders")

    assert response.status_code == 200
    assert calls["n"] == 3  # two retries + the success
    client.close()


def test_post_is_never_retried_through_client(generated_client_module) -> None:
    """The GET-only retry rule applies through the templated client too."""
    runtime = importlib.import_module("acmecli.base.transport")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    client = generated_client_module.AcmeClientBase(
        "https://api.example.com",
        transport=transport,
        retries=runtime.RetryPolicy(total=5, backoff=0.0),
    )
    response = client._http.post("/orders", json={})

    assert response.status_code == 503
    assert calls["n"] == 1
    client.close()


def test_async_client_constructable(generated_client_module) -> None:
    """The async sibling instantiates and exposes the same shape API."""
    client = generated_client_module.AsyncAcmeClientBase("https://api.example.com")

    assert client.base_url == "https://api.example.com"
    assert client.shape == "models"
    sibling = client.with_shape("dicts")
    assert sibling.shape == "dicts"
    assert sibling._http is client._http
