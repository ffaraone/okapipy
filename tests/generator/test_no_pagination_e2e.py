"""End-to-end tests for collections generated against non-paginated fetches.

A collection whose fetch operation carries `x-okapipy-paginated: false`
is emitted with a stripped surface — no `get_page`, no `page_size`, no
iterator state machine. Instead, every read accessor (`first`, `count`,
`exists`, iteration) issues a single GET and works on the materialised
response. These tests pin that behavior on the real generated client.
"""

from __future__ import annotations

from types import ModuleType

import httpx
import pytest


def test_no_pagination_iter_yields_all_items_in_one_request(
    no_pagination_client_module: ModuleType,
) -> None:
    """Sync iteration issues exactly one GET and yields every item in the envelope."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"items": [{"id": "1"}, {"id": "2"}, {"id": "3"}]},
        )

    transport = httpx.MockTransport(handler)
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    items = list(client.items)

    assert [item.id for item in items] == ["1", "2", "3"]
    assert calls["n"] == 1
    client.close()


def test_no_pagination_count_returns_envelope_size(
    no_pagination_client_module: ModuleType,
) -> None:
    """`count()` returns the materialised length of the single response."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"items": [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}]},
        )
    )
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    assert client.items.count() == 4
    client.close()


def test_no_pagination_first_returns_head_item(
    no_pagination_client_module: ModuleType,
) -> None:
    """`first()` returns the first item from the single fetch."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"items": [{"id": "alpha"}, {"id": "beta"}]},
        )
    )
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    head = client.items.first()

    assert head is not None
    assert head.id == "alpha"
    client.close()


def test_no_pagination_first_returns_none_on_empty(
    no_pagination_client_module: ModuleType,
) -> None:
    """`first()` returns `None` when the envelope holds an empty list."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"items": []})
    )
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    assert client.items.first() is None
    client.close()


def test_no_pagination_exists_reflects_envelope_population(
    no_pagination_client_module: ModuleType,
) -> None:
    """`exists()` is `True` when the response has items, `False` when empty."""
    responses = iter(
        [
            {"items": [{"id": "x"}]},
            {"items": []},
        ]
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=next(responses))
    )
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    assert client.items.exists() is True
    assert client.items.exists() is False
    client.close()


def test_no_pagination_collection_drops_pagination_only_methods(
    no_pagination_client_module: ModuleType,
) -> None:
    """The emitted collection class has no `get_page` or `page_size` attribute."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"items": []})
    )
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )
    collection = client.items

    assert not hasattr(collection, "get_page")
    assert not hasattr(collection, "page_size")
    assert not hasattr(collection, "current_page_size")
    client.close()


def test_no_pagination_count_does_not_consult_strategy(
    no_pagination_client_module: ModuleType,
) -> None:
    """`count()` works even when the pagination strategy has no count source.

    Cursor pagination's `supports_count` is `False` unless `content_range`
    is configured. If the generated `count()` still routed through the
    strategy, this call would raise `UnsupportedPaginationError`; for a
    non-paginated collection it must not.
    """
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"items": [{"id": "1"}, {"id": "2"}]})
    )
    client = no_pagination_client_module.NoPagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=no_pagination_client_module.CursorPagination(
            default_page_size=10
        ),
    )

    assert client.items.count() == 2
    client.close()


@pytest.mark.asyncio
async def test_no_pagination_async_iter_yields_all_items_in_one_request(
    no_pagination_client_module: ModuleType,
) -> None:
    """Async iteration issues exactly one GET and yields every item."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"items": [{"id": "1"}, {"id": "2"}]},
        )

    transport = httpx.MockTransport(handler)
    client = no_pagination_client_module.AsyncNoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    items = [item async for item in client.items]

    assert [item.id for item in items] == ["1", "2"]
    assert calls["n"] == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_no_pagination_async_count_returns_envelope_size(
    no_pagination_client_module: ModuleType,
) -> None:
    """Async `count()` returns the materialised length of the single response."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"items": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        )
    )
    client = no_pagination_client_module.AsyncNoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    assert await client.items.count() == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_no_pagination_async_first_returns_head_item(
    no_pagination_client_module: ModuleType,
) -> None:
    """Async `first()` returns the head item from the single fetch."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"items": [{"id": "alpha"}, {"id": "beta"}]}
        )
    )
    client = no_pagination_client_module.AsyncNoPagClientBase(
        "https://api.example.com",
        transport=transport,
    )

    head = await client.items.first()

    assert head is not None
    assert head.id == "alpha"
    await client.aclose()
