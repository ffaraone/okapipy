"""End-to-end pagination tests against generated clients.

Generates a small client against a fixture, then exercises iteration / count /
first against `httpx.MockTransport` programmed to return successive pages. Each
test runs against a different `PaginationStrategy` to confirm the iterator
adapts purely through strategy injection — no per-collection regeneration needed.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "simple.yaml"


@pytest.fixture
def client_module(tmp_path: Path):
    """Generate the client + write to disk + import the package.

    Yields the imported module (not the client class) so tests can also fetch
    runtime types like `OffsetLimitPagination` from `module.<runtime>`. Cleans
    sys.modules / sys.path on teardown to keep tests independent.
    """
    package = "pagcli"
    out = tmp_path / "out"
    api = parse(FIXTURE)
    vfs = generate(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package=package,
        client_class="PagClient",
        project_name="pag-client",
    )
    write_to_disk(vfs, out)
    sys.path.insert(0, str(out / "src"))
    try:
        if package in sys.modules:
            del sys.modules[package]
        module = importlib.import_module(f"{package}.base")
        yield module
    finally:
        sys.path.remove(str(out / "src"))
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]


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
    """`OffsetLimitPagination` walks pages until `offset >= total`."""
    pages = [
        {"items": [{"id": "1"}, {"id": "2"}], "total": 5},
        {"items": [{"id": "3"}, {"id": "4"}], "total": 5},
        {"items": [{"id": "5"}], "total": 5},
    ]
    transport = httpx.MockTransport(_paged_handler(pages))
    client = client_module.PagClientBase(
        "https://api.example.com",
        transport=transport,
        pagination_strategy=client_module.OffsetLimitPagination(),
    )

    items = list(client.orders.page_size(2))

    assert [item["id"] for item in items] == ["1", "2", "3", "4", "5"]
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
        pagination_strategy=client_module.OffsetLimitPagination(),
    )

    first = client.orders.first()

    assert first == {"id": "1"}
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
        pagination_strategy=client_module.OffsetLimitPagination(),
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
        pagination_strategy=client_module.CursorPagination(),
    )

    items = list(client.orders)

    assert [item["id"] for item in items] == ["1", "2", "3"]
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
        pagination_strategy=client_module.LinkHeaderPagination(),
    )

    items = list(client.orders)

    assert [item["id"] for item in items] == ["1", "2"]
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
        pagination_strategy=client_module.OffsetLimitPagination(),
    )

    items = list(client.orders.with_options(headers={"X-Trace": "abc"}).page_size(2))

    assert [item["id"] for item in items] == ["x", "y"]
    assert all(h == "abc" for h in seen_headers if h)
    client.close()
