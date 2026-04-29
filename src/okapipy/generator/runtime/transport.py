"""Retry policy and `httpx` transport wrapper.

Per `generator.md` §6.3, retries apply to **`GET` only**. Other methods bypass
the retry wrapper entirely. The wrapper sits in front of any transport the user
configures and re-issues the request when the response status is in
`retry_on_status` or the underlying transport raises one of `retry_on_exceptions`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for `RetryTransport`.

    `total = 0` disables retries. `backoff` is the base seconds for exponential
    delay between attempts (`delay = backoff * 2**attempt`).
    """

    total: int = 0
    backoff: float = 0.0
    retry_on_status: frozenset[int] = field(
        default_factory=lambda: frozenset({502, 503, 504})
    )
    retry_on_exceptions: tuple[type[BaseException], ...] = (httpx.TransportError,)


class RetryTransport(httpx.BaseTransport):
    """A wrapper transport that retries `GET` requests per `RetryPolicy`.

    Any non-GET request is forwarded to the wrapped transport untouched. GET
    requests are retried on matching status codes / exception types up to
    `policy.total` extra attempts, with exponential backoff.
    """

    def __init__(
        self,
        wrapped: httpx.BaseTransport,
        policy: RetryPolicy,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.wrapped = wrapped
        self.policy = policy
        self._sleep: Callable[[float], None] = sleep if sleep is not None else time.sleep

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() != "GET" or self.policy.total <= 0:
            return self.wrapped.handle_request(request)
        attempt = 0
        last_exc: BaseException | None = None
        while attempt <= self.policy.total:
            try:
                response = self.wrapped.handle_request(request)
            except self.policy.retry_on_exceptions as exc:
                last_exc = exc
                if attempt == self.policy.total:
                    raise
                self._wait(attempt)
                attempt += 1
                continue
            if response.status_code not in self.policy.retry_on_status:
                return response
            if attempt == self.policy.total:
                return response
            response.close()
            self._wait(attempt)
            attempt += 1
        # Loop should always return or raise; this satisfies mypy.
        raise last_exc if last_exc is not None else RuntimeError("unreachable")

    def close(self) -> None:
        self.wrapped.close()

    def _wait(self, attempt: int) -> None:
        if self.policy.backoff <= 0:
            return
        delay = self.policy.backoff * (2**attempt)
        self._sleep(delay)


class AsyncRetryTransport(httpx.AsyncBaseTransport):
    """Async sibling of `RetryTransport`. Same GET-only rule."""

    def __init__(
        self,
        wrapped: httpx.AsyncBaseTransport,
        policy: RetryPolicy,
    ) -> None:
        self.wrapped = wrapped
        self.policy = policy

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import asyncio  # local import keeps sync-only users out of asyncio

        if request.method.upper() != "GET" or self.policy.total <= 0:
            return await self.wrapped.handle_async_request(request)
        attempt = 0
        last_exc: BaseException | None = None
        while attempt <= self.policy.total:
            try:
                response = await self.wrapped.handle_async_request(request)
            except self.policy.retry_on_exceptions as exc:
                last_exc = exc
                if attempt == self.policy.total:
                    raise
                if self.policy.backoff > 0:
                    await asyncio.sleep(self.policy.backoff * (2**attempt))
                attempt += 1
                continue
            if response.status_code not in self.policy.retry_on_status:
                return response
            if attempt == self.policy.total:
                return response
            await response.aclose()
            if self.policy.backoff > 0:
                await asyncio.sleep(self.policy.backoff * (2**attempt))
            attempt += 1
        raise last_exc if last_exc is not None else RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self.wrapped.aclose()


# Re-export for completeness; helps mypy notice these are public surface.
__all__: Sequence[str] = ("RetryPolicy", "RetryTransport", "AsyncRetryTransport")
