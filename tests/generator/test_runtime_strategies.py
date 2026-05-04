"""Encoding behavior of built-in pagination/filter/sort strategies."""

from __future__ import annotations

import json

import httpx
import pytest

from okapipy.generator.runtime.exceptions import (
    UnsupportedFilterError,
    UnsupportedSortError,
)
from okapipy.generator.runtime.filters import Filter, Search
from okapipy.generator.runtime.sort import Sort
from okapipy.generator.runtime.strategies import (
    CommaSignedSort,
    CursorPagination,
    JsonApiSort,
    JsonFilterStrategy,
    KeyDirectionSort,
    KeyOpValueFilter,
    KeyValueFilter,
    LinkHeaderPagination,
    OffsetLimitPagination,
    PageNumberPagination,
    SearchFilterStrategy,
)


def _response(
    body: dict | list | None = None,
    *,
    headers: dict[str, str] | None = None,
    status_code: int = 200,
) -> httpx.Response:
    """Build a synthetic `httpx.Response` for strategy tests."""
    return httpx.Response(
        status_code=status_code,
        json=body if body is not None else [],
        headers=headers or {},
    )


# --------------------------------------------------------------------------- #
# Pagination                                                                  #
# --------------------------------------------------------------------------- #


def test_offset_limit_initial_seeds_offset_zero_and_limit() -> None:
    """`OffsetLimitPagination.initial(page_size)` seeds offset=0 + the requested limit."""
    strat = OffsetLimitPagination()

    params = strat.initial(page_size=50)

    assert params == {"offset": 0, "limit": 50}


def test_offset_limit_next_advances_until_total_reached() -> None:
    """Subsequent calls advance offset by the last limit until total is reached."""
    strat = OffsetLimitPagination(total_field="total")
    response = _response({"items": [{"id": 1}, {"id": 2}], "total": 2})

    next_params = strat.next(response, last_params={"offset": 0, "limit": 2})

    assert next_params is None  # total reached


def test_offset_limit_extracts_total_from_envelope_field() -> None:
    """`extract_count` reads `total_field` from the response envelope."""
    strat = OffsetLimitPagination(total_field="total")
    response = _response({"items": [], "total": 1234})

    assert strat.extract_count(response) == 1234


def test_offset_limit_supports_count_via_content_range() -> None:
    """`content_range=True` enables count via the RFC 7233 `Content-Range` header."""
    strat = OffsetLimitPagination(total_field=None, content_range=True)
    response = _response(headers={"Content-Range": "items 0-49/4321"})

    assert strat.supports_count is True
    assert strat.extract_count(response) == 4321


def test_offset_limit_supports_count_via_header() -> None:
    """`total_header='X-Total-Count'` enables count via a plain numeric header."""
    strat = OffsetLimitPagination(total_field=None, total_header="X-Total-Count")
    response = _response(headers={"X-Total-Count": "999"})

    assert strat.extract_count(response) == 999


def test_offset_limit_no_count_source_means_supports_count_false() -> None:
    """Without any total source configured, `supports_count` is False."""
    strat = OffsetLimitPagination(total_field=None)

    assert strat.supports_count is False


def test_page_number_pagination_advances_until_short_page() -> None:
    """A page that returns fewer items than `page_size` ends iteration."""
    strat = PageNumberPagination(page_param="page", page_size_param="per_page")

    next_params = strat.next(
        _response([{"id": 1}, {"id": 2}]),
        last_params={"page": 1, "per_page": 5},
    )

    assert next_params is None


def test_cursor_pagination_uses_next_token_field() -> None:
    """`CursorPagination` follows the `next_cursor` field through the response."""
    strat = CursorPagination(
        cursor_param="page_token", next_cursor_field="next_page_token"
    )

    next_params = strat.next(
        _response({"items": [{"id": 1}], "next_page_token": "abc"}),
        last_params={"page_token": None},
    )

    assert next_params == {"page_token": "abc"}


def test_cursor_pagination_stops_when_no_token() -> None:
    """A response without a next-cursor field ends iteration."""
    strat = CursorPagination()

    next_params = strat.next(
        _response({"items": [], "next_cursor": None}),
        last_params={},
    )

    assert next_params is None


def test_link_header_pagination_extracts_next_url() -> None:
    """`LinkHeaderPagination` returns a `__url__` marker for the iterator to follow."""
    strat = LinkHeaderPagination()
    response = _response(
        {"items": [{"id": 1}]},
        headers={"Link": '<https://api.example.com/orders?page=2>; rel="next"'},
    )

    next_params = strat.next(response, last_params={})

    assert next_params == {"__url__": "https://api.example.com/orders?page=2"}


def test_link_header_pagination_stops_without_next_rel() -> None:
    """Iteration stops when no `rel="next"` link is present."""
    strat = LinkHeaderPagination()
    response = _response({"items": []}, headers={"Link": '<...>; rel="prev"'})

    assert strat.next(response, last_params={}) is None


# --------------------------------------------------------------------------- #
# Filter                                                                      #
# --------------------------------------------------------------------------- #


def test_key_value_filter_encodes_conjunctive_equality() -> None:
    """`KeyValueFilter` flattens `&`-composed equality leaves into a single dict."""
    expr = Filter(status="open") & Filter(customer_id=42)

    params = KeyValueFilter().encode(expr)

    assert params == {"status": "open", "customer_id": 42}


def test_key_value_filter_rejects_or_and_not() -> None:
    """`KeyValueFilter` raises `UnsupportedFilterError` on OR/NOT nodes."""
    expr = Filter(status="open") | Filter(status="pending")

    with pytest.raises(UnsupportedFilterError):
        KeyValueFilter().encode(expr)


def test_key_value_filter_rejects_operator_suffix() -> None:
    """Operator-suffix keys (`__gte`, `__in`) are out of scope for `KeyValueFilter`."""
    expr = Filter(created_at__gte="2026-01-01")

    with pytest.raises(UnsupportedFilterError):
        KeyValueFilter().encode(expr)


def test_key_op_value_filter_accepts_operator_suffix() -> None:
    """`KeyOpValueFilter` passes operator-suffix keys through verbatim."""
    expr = Filter(created_at__gte="2026-01-01") & Filter(status__in=["open", "pending"])

    params = KeyOpValueFilter().encode(expr)

    assert params == {
        "created_at__gte": "2026-01-01",
        "status__in": ["open", "pending"],
    }


def test_search_filter_strategy_emits_q_param() -> None:
    """`SearchFilterStrategy` accepts a single `Search(...)` leaf."""
    expr = Search("running shoes")

    params = SearchFilterStrategy(param="q").encode(expr)

    assert params == {"q": "running shoes"}


def test_search_filter_rejects_compound_expressions() -> None:
    """A search filter strategy cannot encode compound expressions."""
    expr = Search("a") & Search("b")

    with pytest.raises(UnsupportedFilterError):
        SearchFilterStrategy().encode(expr)


def test_json_filter_strategy_round_trips_expression_tree() -> None:
    """`JsonFilterStrategy` serializes the entire `Filter` tree as JSON."""
    expr = Filter(status="open") | Filter(status="pending")

    params = JsonFilterStrategy(param="filter").encode(expr)

    decoded = json.loads(params["filter"])
    assert decoded == {
        "or": [{"status": "open"}, {"status": "pending"}],
    }


def test_filter_strategies_treat_none_as_empty() -> None:
    """All filter strategies emit `{}` when the user did not call `.filter(...)`."""
    assert KeyValueFilter().encode(None) == {}
    assert KeyOpValueFilter().encode(None) == {}
    assert SearchFilterStrategy().encode(None) == {}
    assert JsonFilterStrategy().encode(None) == {}


# --------------------------------------------------------------------------- #
# Sort                                                                        #
# --------------------------------------------------------------------------- #


def test_comma_signed_sort_renders_signed_csv() -> None:
    """`CommaSignedSort` joins terms with commas; descending gets a leading `-`."""
    expr = Sort("-created_at") + Sort("id")

    params = CommaSignedSort(param="sort").encode(expr)

    assert params == {"sort": "-created_at,id"}


def test_key_direction_sort_emits_two_params() -> None:
    """`KeyDirectionSort` emits separate field/direction parameters."""
    expr = Sort("-created_at")

    params = KeyDirectionSort().encode(expr)

    assert params == {"order_by": "created_at", "order": "desc"}


def test_key_direction_sort_refuses_multi_term() -> None:
    """`KeyDirectionSort` cannot represent multiple sort fields."""
    expr = Sort("a") + Sort("b")

    with pytest.raises(UnsupportedSortError):
        KeyDirectionSort().encode(expr)


def test_json_api_sort_matches_jsonapi_format() -> None:
    """`JsonApiSort` matches the JSON:API sort encoding (signed, comma-joined)."""
    expr = Sort("-created_at") + Sort("name")

    params = JsonApiSort().encode(expr)

    assert params == {"sort": "-created_at,name"}


def test_sort_strategies_treat_empty_sort_as_no_op() -> None:
    """An empty `Sort()` (no terms) results in no params from any sort strategy."""
    assert CommaSignedSort().encode(None) == {}
    assert CommaSignedSort().encode(Sort()) == {}
    assert KeyDirectionSort().encode(None) == {}
    assert JsonApiSort().encode(Sort()) == {}
