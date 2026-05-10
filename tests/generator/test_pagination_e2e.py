"""End-to-end pagination tests against generated clients.

Generates a small client against a fixture, then exercises iteration / count /
first against `httpx.MockTransport` programmed to return successive pages. Each
test runs against a different `PaginationStrategy` to confirm the iterator
adapts purely through strategy injection — no per-collection regeneration needed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest


def _paged_handler(
    pages: list[dict[str, Any]],
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a MockTransport handler that returns `pages[i]` on the i-th call.

    Independent of pagination strategy: each test programs the handler to
    respond as if the strategy's expected query params are correct. The handler
    returns the same payload regardless of which params come in — tests assert
    on the sequence of items, not on URL inspection (those tests live in the
    runtime strategy unit tests).
    """
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = state["i"]
        state["i"] += 1
        return httpx.Response(200, json=pages[min(i, len(pages) - 1)])

    return handler


def test_offset_limit_iteration_walks_all_pages(client_module) -> None:
    """`LimitOffsetPagination` walks pages until `offset >= total`."""
    pages = [
        {"items": [{"id": "1"}, {"id": "2"}], "total": 5},
        {"items": [{"id": "3"}, {"id": "4"}], "total": 5},
        {"items": [{"id": "5"}], "total": 5},
    ]
    transport = httpx.MockTransport(_paged_handler(pages))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    items = list(client.orders.page_size(2))

    assert [item.id for item in items] == ["1", "2", "3", "4", "5"]
    client.close()


def test_first_short_circuits_after_one_page(client_module) -> None:
    """`first()` returns the first item without walking subsequent pages."""
    pages = [
        {"items": [{"id": "1"}, {"id": "2"}], "total": 100},
    ]
    transport = httpx.MockTransport(_paged_handler(pages))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    first = client.orders.first()

    assert first is not None
    assert first.id == "1"
    client.close()


def test_first_requests_only_one_item_via_offset_limit(client_module) -> None:
    """`first()` overrides the strategy's `default_page_size` and asks for `limit=1`.

    Regression: the previous implementation iterated with the configured page
    size (here: 100), pulling 99 items the caller would never see. `first()`
    should fetch a single-item page so the server only ships what's needed.
    """
    seen_limits: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(request.url.params.get("limit"))
        return httpx.Response(200, json={"items": [{"id": "1"}], "total": 100})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    first = client.orders.first()

    assert first is not None
    assert first.id == "1"
    assert seen_limits == ["1"]
    client.close()


def test_first_requests_only_one_item_via_page_number(client_module) -> None:
    """`first()` flows the size-1 override through `PageNumberPagination` as `page_size=1`."""
    seen_sizes: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_sizes.append(request.url.params.get("page_size"))
        return httpx.Response(200, json={"items": [{"id": "1"}], "total": 100})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.PageNumberPagination(default_page_size=50),
    )

    first = client.orders.first()

    assert first is not None
    assert first.id == "1"
    assert seen_sizes == ["1"]
    client.close()


def test_first_restores_user_page_size_after_call(client_module) -> None:
    """`first()` reverts `current_page_size` so a later iteration honors the user's choice."""
    seen_limits: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(request.url.params.get("limit"))
        return httpx.Response(200, json={"items": [{"id": "1"}], "total": 1})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )
    collection = client.orders.page_size(25)

    collection.first()
    list(collection)

    # Two requests: first() forces limit=1, the subsequent iteration honors page_size(25).
    assert seen_limits == ["1", "25"]
    client.close()


def test_count_uses_dedicated_minimal_request(client_module) -> None:
    """`count()` issues one `limit=1` request and reads `total` from the envelope."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # The strategy sends `limit=1, offset=0` for count — we can't easily
        # validate the URL params here, but we can validate it was a single GET.
        return httpx.Response(200, json={"items": [{"id": "1"}], "total": 4321})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    total = client.orders.count()

    assert total == 4321
    assert calls["n"] == 1
    client.close()


def test_cursor_pagination_follows_next_token_until_absent(client_module) -> None:
    """`CursorPagination` walks until the response yields no `next_cursor`."""
    pages: list[dict[str, Any]] = [
        {"items": [{"id": "1"}], "next_cursor": "tok-2"},
        {"items": [{"id": "2"}], "next_cursor": "tok-3"},
        {"items": [{"id": "3"}], "next_cursor": None},
    ]
    transport = httpx.MockTransport(_paged_handler(pages))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.CursorPagination(default_page_size=10),
    )

    items = list(client.orders)

    assert [item.id for item in items] == ["1", "2", "3"]
    client.close()


def test_link_header_pagination_follows_rel_next(client_module) -> None:
    """`LinkHeaderPagination` follows `Link: <next>; rel="next"` until absent."""
    next_url = "https://api.example.com/orders?page=2"
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = state["i"]
        state["i"] += 1
        if i == 0:
            return httpx.Response(
                200,
                json={"items": [{"id": "1"}]},
                headers={"Link": f'<{next_url}>; rel="next"'},
            )
        return httpx.Response(200, json={"items": [{"id": "2"}]})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LinkHeaderPagination(default_page_size=10),
    )

    items = list(client.orders)

    assert [item.id for item in items] == ["1", "2"]
    client.close()


def test_filter_strategy_default_page_size_seeds_limit_param(client_module) -> None:
    """`LimitOffsetPagination(default_page_size=...)` seeds `limit` without `.page_size(...)`."""
    seen_limits: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(request.url.params.get("limit"))
        return httpx.Response(200, json={"items": [], "total": 0})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=42),
    )

    list(client.orders)

    assert seen_limits == ["42"]
    client.close()


def test_raw_query_filter_strategy_appends_to_url(client_module) -> None:
    """Filter strategy emitting `raw_query` is appended verbatim to the request URL.

    Mirrors RQL: `/orders?and(eq(field1,value1))&offset=0&limit=2`. The raw
    fragment must not be URL-encoded as a key=value param.
    """
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": [], "total": 0})

    transport = httpx.MockTransport(handler)
    Filter = client_module.Filter
    FilterEncoding = client_module.FilterEncoding

    class RqlLike:
        def encode(self, f):
            if f is None:
                return FilterEncoding()
            terms = [f"eq({k},{v})" for k, v in f.kwargs.items()]
            return FilterEncoding(raw_query="and(" + ",".join(terms) + ")")

    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
        filter_strategy=RqlLike(),
    )

    list(client.orders.filter(Filter(field1="value1", field2="value2")).page_size(2))

    assert len(captured) == 1
    url = captured[0]
    raw_query = url.raw_path.decode("ascii")
    # Path + query is something like
    # `/orders?and(eq(field1,value1),eq(field2,value2))&offset=0&limit=2`.
    assert raw_query.startswith("/orders?and(eq(field1,value1),eq(field2,value2))")
    assert "offset=0" in raw_query
    assert "limit=2" in raw_query
    client.close()


def test_raw_query_filter_strategy_count_request(client_module) -> None:
    """`count()` also routes the filter-strategy raw fragment into the request URL."""
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": [{"id": "1"}], "total": 17})

    transport = httpx.MockTransport(handler)
    Filter = client_module.Filter
    FilterEncoding = client_module.FilterEncoding

    class RqlLike:
        def encode(self, f):
            if f is None:
                return FilterEncoding()
            terms = [f"eq({k},{v})" for k, v in f.kwargs.items()]
            return FilterEncoding(raw_query="and(" + ",".join(terms) + ")")

    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
        filter_strategy=RqlLike(),
    )

    total = client.orders.filter(Filter(status="open")).count()

    assert total == 17
    raw_query = captured[0].raw_path.decode("ascii")
    assert raw_query.startswith("/orders?and(eq(status,open))")
    assert "limit=1" in raw_query
    assert "offset=0" in raw_query
    client.close()


def test_raw_query_sort_strategy_appends_to_url(client_module) -> None:
    """Sort strategy emitting `raw_query` is appended verbatim to the request URL.

    Mirrors the filter raw_query support: an RQL-style ordering expression
    (`ordering(+name,-created_at)`) must reach the server with parentheses
    and commas intact instead of being URL-encoded as a `key=value` param.
    """
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": [], "total": 0})

    transport = httpx.MockTransport(handler)
    Sort = client_module.Sort
    SortEncoding = client_module.SortEncoding

    class RqlOrdering:
        def encode(self, s):
            if not s:
                return SortEncoding()
            terms = [
                f"-{field_}" if direction == "desc" else f"+{field_}"
                for field_, direction in s.terms
            ]
            return SortEncoding(raw_query="ordering(" + ",".join(terms) + ")")

    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
        sort_strategy=RqlOrdering(),
    )

    list(client.orders.order_by(Sort("-created_at") + Sort("name")).page_size(2))

    assert len(captured) == 1
    raw_query = captured[0].raw_path.decode("ascii")
    assert raw_query.startswith("/orders?ordering(-created_at,+name)")
    assert "offset=0" in raw_query
    assert "limit=2" in raw_query
    client.close()


def test_raw_query_filter_and_sort_compose_with_ampersand(client_module) -> None:
    """Filter and sort raw_query fragments are concatenated with `&` in the URL.

    Both strategies emit verbatim fragments: they must coexist in the query
    string alongside ordinary pagination params, joined by `&` separators.
    """
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": [], "total": 0})

    transport = httpx.MockTransport(handler)
    Filter = client_module.Filter
    FilterEncoding = client_module.FilterEncoding
    Sort = client_module.Sort
    SortEncoding = client_module.SortEncoding

    class RqlFilter:
        def encode(self, f):
            if f is None:
                return FilterEncoding()
            terms = [f"eq({k},{v})" for k, v in f.kwargs.items()]
            return FilterEncoding(raw_query="and(" + ",".join(terms) + ")")

    class RqlOrdering:
        def encode(self, s):
            if not s:
                return SortEncoding()
            terms = [
                f"-{field_}" if direction == "desc" else f"+{field_}"
                for field_, direction in s.terms
            ]
            return SortEncoding(raw_query="ordering(" + ",".join(terms) + ")")

    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
        filter_strategy=RqlFilter(),
        sort_strategy=RqlOrdering(),
    )

    list(
        client.orders.filter(Filter(status="open"))
        .order_by(Sort("-created_at"))
        .page_size(2)
    )

    assert len(captured) == 1
    raw_query = captured[0].raw_path.decode("ascii")
    assert raw_query.startswith("/orders?and(eq(status,open))&ordering(-created_at)")
    assert "offset=0" in raw_query
    assert "limit=2" in raw_query
    client.close()


def test_exists_true_when_strategy_reports_positive_total(client_module) -> None:
    """`exists()` returns True via `count()` when the strategy reports a positive total."""
    transport = httpx.MockTransport(
        _paged_handler([{"items": [{"id": "1"}], "total": 42}])
    )
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    assert client.orders.exists() is True
    client.close()


def test_exists_false_when_strategy_reports_zero_total(client_module) -> None:
    """`exists()` returns False when the strategy reports a total of zero."""
    transport = httpx.MockTransport(_paged_handler([{"items": [], "total": 0}]))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    assert client.orders.exists() is False
    client.close()


def test_count_raises_unsupported_pagination_error_when_no_count_source(
    client_module,
) -> None:
    """`count()` raises `UnsupportedPaginationError` (not `NotImplementedError`).

    Regression: the previous implementation raised `NotImplementedError`, which
    misleadingly suggests the feature is a TODO rather than a wire-protocol
    constraint of the configured strategy.
    """
    transport = httpx.MockTransport(_paged_handler([{"items": []}]))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        # CursorPagination with content_range=False has no count source.
        pagination_strategy=client_module.CursorPagination(default_page_size=10),
    )

    with pytest.raises(client_module.UnsupportedPaginationError):
        client.orders.count()
    client.close()


def test_get_page_zero_indexed_offset_for_limit_offset_strategy(client_module) -> None:
    """`get_page(n)` issues a single request with `offset = n * page_size`."""
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(
            200, json={"items": [{"id": "x"}, {"id": "y"}], "total": 100}
        )

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    page = client.orders.page_size(20).get_page(3)

    assert [item.id for item in page] == ["x", "y"]
    assert len(captured) == 1
    assert captured[0].params.get("offset") == "60"
    assert captured[0].params.get("limit") == "20"
    client.close()


def test_get_page_uses_strategy_default_page_size_when_unset(client_module) -> None:
    """`get_page(n)` falls back to the strategy's `default_page_size` without `.page_size(...)`."""
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": [], "total": 0})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=42),
    )

    client.orders.get_page(2)

    assert captured[0].params.get("offset") == "84"
    assert captured[0].params.get("limit") == "42"
    client.close()


def test_get_page_zero_indexed_for_page_number_strategy(client_module) -> None:
    """`get_page(0)` lands on `start_page`; `get_page(n)` lands on `start_page + n`."""
    captured: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url)
        return httpx.Response(200, json={"items": [{"id": "p"}], "total": 100})

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.PageNumberPagination(default_page_size=10),
    )

    client.orders.page_size(5).get_page(0)
    client.orders.page_size(5).get_page(4)

    assert captured[0].params.get("page") == "1"  # start_page=1 + 0
    assert captured[0].params.get("page_size") == "5"
    assert captured[1].params.get("page") == "5"  # start_page=1 + 4
    client.close()


def test_get_page_raises_unsupported_for_cursor_strategy(client_module) -> None:
    """`get_page(...)` rejects sequential strategies up front.

    Cursor pagination cannot reach page N without first fetching the cursor
    returned by page N-1, so the collection refuses the call rather than
    silently walking pages — that would defeat the parallelism use case.
    """
    transport = httpx.MockTransport(_paged_handler([{"items": []}]))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.CursorPagination(default_page_size=10),
    )

    with pytest.raises(
        client_module.UnsupportedPaginationError, match="random page access"
    ):
        client.orders.get_page(2)
    client.close()


def test_get_page_raises_unsupported_for_link_header_strategy(client_module) -> None:
    """`get_page(...)` also rejects link-header pagination — it is sequential."""
    transport = httpx.MockTransport(_paged_handler([{"items": []}]))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LinkHeaderPagination(default_page_size=10),
    )

    with pytest.raises(
        client_module.UnsupportedPaginationError, match="random page access"
    ):
        client.orders.get_page(1)
    client.close()


def test_get_page_threads_filter_and_with_options_into_request(client_module) -> None:
    """`get_page(n)` carries filter params and `with_options(headers=...)` overrides."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"items": [], "total": 0})

    transport = httpx.MockTransport(handler)
    Filter = client_module.Filter
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    (
        client.orders.filter(Filter(status="open"))
        .with_options(headers={"X-Trace": "abc"})
        .page_size(10)
        .get_page(2)
    )

    assert captured[0].url.params.get("status") == "open"
    assert captured[0].url.params.get("offset") == "20"
    assert captured[0].url.params.get("limit") == "10"
    assert captured[0].headers.get("X-Trace") == "abc"
    client.close()


def test_with_options_seeds_overrides_for_every_page(client_module) -> None:
    """`with_options(headers=...)` is forwarded to every page request."""
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("X-Trace", ""))
        if len(seen_headers) >= 2:
            return httpx.Response(200, json={"items": [], "total": 2})
        return httpx.Response(
            200, json={"items": [{"id": "x"}, {"id": "y"}], "total": 2}
        )

    transport = httpx.MockTransport(handler)
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.LimitOffsetPagination(default_page_size=100),
    )

    items = list(client.orders.with_options(headers={"X-Trace": "abc"}).page_size(2))

    assert [item.id for item in items] == ["x", "y"]
    assert all(h == "abc" for h in seen_headers if h)
    client.close()
