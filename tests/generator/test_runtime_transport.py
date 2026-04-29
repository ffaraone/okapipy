"""GET-only retry transport (`RetryTransport`)."""

from __future__ import annotations

import httpx
import pytest

from okapipy.generator.runtime.transport import RetryPolicy, RetryTransport


class _CountingTransport(httpx.BaseTransport):
    """A transport that returns a scripted sequence of responses (or raises)."""

    def __init__(self, scripts: list[httpx.Response | Exception]) -> None:
        self.scripts = list(scripts)
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        item = self.scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _server_error(status: int = 503) -> httpx.Response:
    return httpx.Response(status, text="upstream down")


def test_get_is_retried_on_matching_status_until_total() -> None:
    """A `GET` is retried up to `policy.total` times on retryable status codes."""
    inner = _CountingTransport([_server_error(503), _server_error(503), _ok()])
    transport = RetryTransport(inner, RetryPolicy(total=2), sleep=lambda _delay: None)

    response = transport.handle_request(httpx.Request("GET", "https://api/x"))

    assert response.status_code == 200
    assert inner.calls == 3


def test_get_returns_last_response_when_retries_exhausted() -> None:
    """When retries are exhausted, the last (still-failing) response is returned."""
    inner = _CountingTransport([_server_error(503), _server_error(503)])
    transport = RetryTransport(inner, RetryPolicy(total=1), sleep=lambda _delay: None)

    response = transport.handle_request(httpx.Request("GET", "https://api/x"))

    assert response.status_code == 503
    assert inner.calls == 2


def test_post_is_never_retried_even_on_503() -> None:
    """`POST` bypasses the retry wrapper — same for PUT/PATCH/DELETE/HEAD/OPTIONS."""
    inner = _CountingTransport([_server_error(503)])
    transport = RetryTransport(inner, RetryPolicy(total=3), sleep=lambda _delay: None)

    response = transport.handle_request(httpx.Request("POST", "https://api/x"))

    assert response.status_code == 503
    assert inner.calls == 1


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def test_non_get_methods_pass_through_unconditionally(method: str) -> None:
    """Per generator.md §6.3 the GET-only rule applies to all other methods too."""
    inner = _CountingTransport([_server_error(503)])
    transport = RetryTransport(inner, RetryPolicy(total=5), sleep=lambda _delay: None)

    response = transport.handle_request(httpx.Request(method, "https://api/x"))

    assert response.status_code == 503
    assert inner.calls == 1


def test_transport_error_during_get_is_retried_then_raised() -> None:
    """Retryable exceptions count toward the budget; when exhausted they re-raise."""
    inner = _CountingTransport(
        [
            httpx.ConnectError("fail 1"),
            httpx.ConnectError("fail 2"),
            httpx.ConnectError("fail 3"),
        ]
    )
    transport = RetryTransport(inner, RetryPolicy(total=2), sleep=lambda _delay: None)

    with pytest.raises(httpx.ConnectError):
        transport.handle_request(httpx.Request("GET", "https://api/x"))
    assert inner.calls == 3


def test_2xx_short_circuits_retry_loop() -> None:
    """A successful response is returned immediately — no extra calls."""
    inner = _CountingTransport([_ok()])
    transport = RetryTransport(inner, RetryPolicy(total=5), sleep=lambda _delay: None)

    response = transport.handle_request(httpx.Request("GET", "https://api/x"))

    assert response.status_code == 200
    assert inner.calls == 1


def test_zero_total_disables_retries() -> None:
    """`total=0` is the documented "retries off" value — failures pass through."""
    inner = _CountingTransport([_server_error(503)])
    transport = RetryTransport(inner, RetryPolicy(total=0), sleep=lambda _delay: None)

    response = transport.handle_request(httpx.Request("GET", "https://api/x"))

    assert response.status_code == 503
    assert inner.calls == 1
