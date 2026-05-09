"""End-to-end async-tree tests against generated clients."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


def _async_paged_handler(pages: list[dict]):
    """Same shape as the sync helper; yields page i on the i-th call."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = state["i"]
        state["i"] += 1
        return httpx.Response(200, json=pages[min(i, len(pages) - 1)])

    return handler


def test_async_iteration_walks_all_pages(async_client_module) -> None:
    """`async for` over a collection walks the offset/limit pagination loop."""

    async def run():
        pages = [
            {"items": [{"id": "1"}, {"id": "2"}], "total": 5},
            {"items": [{"id": "3"}, {"id": "4"}], "total": 5},
            {"items": [{"id": "5"}], "total": 5},
        ]
        transport = httpx.MockTransport(_async_paged_handler(pages))
        client_cls = async_client_module.AsyncAsyncCliBase
        async with client_cls("https://api.example.com", transport=transport) as c:
            return [item async for item in c.orders.page_size(2)]

    items = asyncio.run(run())

    assert [it.id for it in items] == ["1", "2", "3", "4", "5"]


def test_async_first_short_circuits(async_client_module) -> None:
    """Async `first()` returns one item without continuing iteration."""

    async def run():
        pages = [{"items": [{"id": "first"}], "total": 99}]
        transport = httpx.MockTransport(_async_paged_handler(pages))
        client_cls = async_client_module.AsyncAsyncCliBase
        async with client_cls("https://api.example.com", transport=transport) as c:
            return await c.orders.first()

    first = asyncio.run(run())

    assert first is not None
    assert first.id == "first"


def test_async_first_requests_only_one_item(async_client_module) -> None:
    """Async `first()` overrides the strategy's `default_page_size` and asks for `limit=1`.

    Regression: pulling a full default-size page just to return one item wastes
    bandwidth. The override must reach the strategy via `current_page_size=1`.
    """
    seen_limits: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(request.url.params.get("limit"))
        return httpx.Response(200, json={"items": [{"id": "first"}], "total": 99})

    async def run() -> Any:
        transport = httpx.MockTransport(handler)
        client_cls = async_client_module.AsyncAsyncCliBase
        async with client_cls(
            "https://api.example.com",
            transport=transport,
            pagination_strategy=async_client_module.LimitOffsetPagination(
                default_page_size=100,
            ),
        ) as c:
            return await c.orders.first()

    first = asyncio.run(run())

    assert first is not None
    assert first.id == "first"
    assert seen_limits == ["1"]


def test_async_count_returns_envelope_total(async_client_module) -> None:
    """Async `count()` issues one request and reads the envelope total field."""

    async def run() -> int:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"items": [{"id": "x"}], "total": 4321}),
        )
        client_cls = async_client_module.AsyncAsyncCliBase
        async with client_cls("https://api.example.com", transport=transport) as c:
            return await c.orders.count()

    total = asyncio.run(run())

    assert total == 4321


def test_async_resource_retrieve(async_client_module) -> None:
    """`async with client: ...; await client.orders[id].retrieve()` works."""

    async def run() -> Any:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"id": "42", "total": 99.99}),
        )
        client_cls = async_client_module.AsyncAsyncCliBase
        async with client_cls("https://api.example.com", transport=transport) as c:
            return await c.orders["42"].retrieve()

    order = asyncio.run(run())

    assert order.id == "42"
    assert order.total == 99.99
