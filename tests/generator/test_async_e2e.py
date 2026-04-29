"""End-to-end async-tree tests against generated clients."""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import httpx
import pytest

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "simple.yaml"


@pytest.fixture
def async_client_module(tmp_path: Path):
    """Generate the client tree, write to disk, and import the package."""
    package = "asynccli"
    out = tmp_path / "out"
    api = parse(FIXTURE)
    vfs = generate(
        api,
        raw_spec=FIXTURE,
        output_dir=out,
        package=package,
        client_class="AsyncCli",
        project_name="async-cli",
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

    async def run() -> list[dict]:
        pages = [
            {"items": [{"id": "1"}, {"id": "2"}], "total": 5},
            {"items": [{"id": "3"}, {"id": "4"}], "total": 5},
            {"items": [{"id": "5"}], "total": 5},
        ]
        transport = httpx.MockTransport(_async_paged_handler(pages))
        async with async_client_module.AsyncAsyncCli(transport=transport) as c:
            return [item async for item in c.orders.page_size(2)]

    items = asyncio.run(run())

    assert [it["id"] for it in items] == ["1", "2", "3", "4", "5"]


def test_async_first_short_circuits(async_client_module) -> None:
    """Async `first()` returns one item without continuing iteration."""

    async def run():
        pages = [{"items": [{"id": "first"}], "total": 99}]
        transport = httpx.MockTransport(_async_paged_handler(pages))
        async with async_client_module.AsyncAsyncCli(transport=transport) as c:
            return await c.orders.first()

    first = asyncio.run(run())

    assert first == {"id": "first"}


def test_async_count_returns_envelope_total(async_client_module) -> None:
    """Async `count()` issues one request and reads the envelope total field."""

    async def run() -> int:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"items": [{"id": "x"}], "total": 4321}),
        )
        async with async_client_module.AsyncAsyncCli(transport=transport) as c:
            return await c.orders.count()

    total = asyncio.run(run())

    assert total == 4321


def test_async_resource_retrieve(async_client_module) -> None:
    """`async with client: ...; await client.orders[id].retrieve()` works."""

    async def run() -> dict:
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"id": "42", "total": 99.99}),
        )
        async with async_client_module.AsyncAsyncCli(transport=transport) as c:
            return await c.orders["42"].retrieve()

    order = asyncio.run(run())

    assert order.id == "42"
    assert order.total == 99.99
