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
    FilterEncoding,
    JsonApiSort,
    JsonFilterStrategy,
    KeyDirectionSort,
    KeyOpValueFilter,
    KeyValueFilter,
    LimitOffsetPagination,
    LinkHeaderPagination,
    PageNumberPagination,
    SearchFilterStrategy,
    SortEncoding,
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


def test_offset_limit_initial_per_call_size_overrides_strategy_default() -> None:
    """`LimitOffsetPagination.initial(page_size)` seeds offset=0 + the requested limit."""
    strat = LimitOffsetPagination(default_page_size=100)

    params = strat.initial(page_size=50)

    assert params == {"offset": 0, "limit": 50}


def test_offset_limit_initial_uses_strategy_default_when_call_size_is_none() -> None:
    """`default_page_size` on the strategy seeds `limit` when no per-call value is given."""
    strat = LimitOffsetPagination(default_page_size=100)

    assert strat.initial(page_size=None) == {"offset": 0, "limit": 100}


def test_offset_limit_default_page_size_is_required() -> None:
    """Constructing `LimitOffsetPagination` without `default_page_size` raises a TypeError.

    The argument is required (not `int | None`) so every emitted request carries
    a known limit instead of falling back to whatever the backend chooses.
    """
    with pytest.raises(TypeError):
        LimitOffsetPagination()  # type: ignore[call-arg]


def test_page_number_initial_uses_strategy_default_page_size() -> None:
    """`PageNumberPagination.default_page_size` seeds the per-page param when call value is None."""
    strat = PageNumberPagination(default_page_size=20)

    assert strat.initial(page_size=None) == {"page": 1, "page_size": 20}
    assert strat.initial(page_size=5) == {"page": 1, "page_size": 5}


def test_cursor_initial_uses_strategy_default_page_size() -> None:
    """`CursorPagination.default_page_size` seeds the page-size param when call value is None."""
    strat = CursorPagination(default_page_size=15)

    assert strat.initial(page_size=None) == {"page_size": 15}
    assert strat.initial(page_size=3) == {"page_size": 3}


def test_cursor_initial_omits_size_when_param_is_disabled() -> None:
    """`page_size_param=None` opts out of sending any size hint."""
    strat = CursorPagination(default_page_size=15, page_size_param=None)

    assert strat.initial(page_size=None) == {}
    assert strat.initial(page_size=3) == {}


def test_link_header_initial_uses_strategy_default_page_size() -> None:
    """`LinkHeaderPagination.default_page_size` seeds the page-size param when no per-call size."""
    strat = LinkHeaderPagination(default_page_size=30)

    assert strat.initial(page_size=None) == {"limit": 30}
    assert strat.initial(page_size=10) == {"limit": 10}


def test_offset_limit_next_advances_until_total_reached() -> None:
    """Subsequent calls advance offset by the last limit until total is reached."""
    strat = LimitOffsetPagination(default_page_size=100, total_field="total")
    response = _response({"items": [{"id": 1}, {"id": 2}], "total": 2})

    next_params = strat.next(response, last_params={"offset": 0, "limit": 2})

    assert next_params is None  # total reached


def test_offset_limit_extracts_total_from_envelope_field() -> None:
    """`extract_count` reads `total_field` from the response envelope."""
    strat = LimitOffsetPagination(default_page_size=100, total_field="total")
    response = _response({"items": [], "total": 1234})

    assert strat.extract_count(response) == 1234


def test_offset_limit_extracts_total_from_dotted_path() -> None:
    """`total_field` accepts a dotted path for envelopes that nest the total."""
    strat = LimitOffsetPagination(
        default_page_size=100, total_field="meta.pagination.total"
    )
    response = _response(
        {"items": [], "meta": {"pagination": {"total": 4242, "page": 1}}}
    )

    assert strat.extract_count(response) == 4242


def test_offset_limit_dotted_path_missing_intermediate_falls_through() -> None:
    """A missing intermediate key falls through to the next configured count source."""
    strat = LimitOffsetPagination(
        default_page_size=100,
        total_field="meta.pagination.total",
        total_header="X-Total-Count",
    )
    response = _response({"items": []}, headers={"X-Total-Count": "77"})

    # `meta.pagination.total` is absent — `total_header` is consulted next.
    assert strat.extract_count(response) == 77


def test_offset_limit_next_uses_dotted_total_to_stop_iteration() -> None:
    """Iteration stops once `offset >= total` even when `total` lives behind a dotted path."""
    strat = LimitOffsetPagination(default_page_size=2, total_field="meta.total")
    response = _response({"items": [{"id": 1}, {"id": 2}], "meta": {"total": 2}})

    assert strat.next(response, last_params={"offset": 0, "limit": 2}) is None


def test_offset_limit_supports_count_via_content_range() -> None:
    """`content_range=True` enables count via the RFC 7233 `Content-Range` header."""
    strat = LimitOffsetPagination(
        default_page_size=100, total_field=None, content_range=True
    )
    response = _response(headers={"Content-Range": "items 0-49/4321"})

    assert strat.supports_count is True
    assert strat.extract_count(response) == 4321


def test_offset_limit_supports_count_via_header() -> None:
    """`total_header='X-Total-Count'` enables count via a plain numeric header."""
    strat = LimitOffsetPagination(
        default_page_size=100, total_field=None, total_header="X-Total-Count"
    )
    response = _response(headers={"X-Total-Count": "999"})

    assert strat.extract_count(response) == 999


def test_offset_limit_no_count_source_means_supports_count_false() -> None:
    """Without any total source configured, `supports_count` is False."""
    strat = LimitOffsetPagination(default_page_size=100, total_field=None)

    assert strat.supports_count is False


def test_page_number_extracts_total_from_dotted_path() -> None:
    """`PageNumberPagination.total_field` also accepts a dotted path."""
    strat = PageNumberPagination(
        default_page_size=20, total_field="meta.pagination.total"
    )
    response = _response(
        {"items": [], "meta": {"pagination": {"total": 999, "page_count": 50}}}
    )

    assert strat.extract_count(response) == 999


def test_page_number_pagination_advances_until_short_page() -> None:
    """A page that returns fewer items than `page_size` ends iteration."""
    strat = PageNumberPagination(
        default_page_size=5, page_param="page", page_size_param="per_page"
    )

    next_params = strat.next(
        _response([{"id": 1}, {"id": 2}]),
        last_params={"page": 1, "per_page": 5},
    )

    assert next_params is None


def test_cursor_pagination_uses_next_token_field() -> None:
    """`CursorPagination` follows the `next_cursor` field through the response."""
    strat = CursorPagination(
        default_page_size=10,
        cursor_param="page_token",
        next_cursor_field="next_page_token",
    )

    next_params = strat.next(
        _response({"items": [{"id": 1}], "next_page_token": "abc"}),
        last_params={"page_token": None},
    )

    assert next_params == {"page_token": "abc"}


def test_cursor_pagination_stops_when_no_token() -> None:
    """A response without a next-cursor field ends iteration."""
    strat = CursorPagination(default_page_size=10)

    next_params = strat.next(
        _response({"items": [], "next_cursor": None}),
        last_params={},
    )

    assert next_params is None


def test_link_header_pagination_extracts_next_url() -> None:
    """`LinkHeaderPagination` returns a `__url__` marker for the iterator to follow."""
    strat = LinkHeaderPagination(default_page_size=10)
    response = _response(
        {"items": [{"id": 1}]},
        headers={"Link": '<https://api.example.com/orders?page=2>; rel="next"'},
    )

    next_params = strat.next(response, last_params={})

    assert next_params == {"__url__": "https://api.example.com/orders?page=2"}


def test_link_header_pagination_stops_without_next_rel() -> None:
    """Iteration stops when no `rel="next"` link is present."""
    strat = LinkHeaderPagination(default_page_size=10)
    response = _response({"items": []}, headers={"Link": '<...>; rel="prev"'})

    assert strat.next(response, last_params={}) is None


# --------------------------------------------------------------------------- #
# Filter                                                                      #
# --------------------------------------------------------------------------- #


def test_key_value_filter_encodes_conjunctive_equality() -> None:
    """`KeyValueFilter` flattens `&`-composed equality leaves into a single params dict."""
    expr = Filter(status="open") & Filter(customer_id=42)

    encoding = KeyValueFilter().encode(expr)

    assert encoding == FilterEncoding(params={"status": "open", "customer_id": 42})
    assert encoding.raw_query is None


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

    encoding = KeyOpValueFilter().encode(expr)

    assert encoding.params == {
        "created_at__gte": "2026-01-01",
        "status__in": ["open", "pending"],
    }
    assert encoding.raw_query is None


def test_search_filter_strategy_emits_q_param() -> None:
    """`SearchFilterStrategy` accepts a single `Search(...)` leaf."""
    expr = Search("running shoes")

    encoding = SearchFilterStrategy(param="q").encode(expr)

    assert encoding.params == {"q": "running shoes"}
    assert encoding.raw_query is None


def test_search_filter_rejects_compound_expressions() -> None:
    """A search filter strategy cannot encode compound expressions."""
    expr = Search("a") & Search("b")

    with pytest.raises(UnsupportedFilterError):
        SearchFilterStrategy().encode(expr)


def test_json_filter_strategy_round_trips_expression_tree() -> None:
    """`JsonFilterStrategy` serializes the entire `Filter` tree as JSON."""
    expr = Filter(status="open") | Filter(status="pending")

    encoding = JsonFilterStrategy(param="filter").encode(expr)

    decoded = json.loads(encoding.params["filter"])
    assert decoded == {
        "or": [{"status": "open"}, {"status": "pending"}],
    }
    assert encoding.raw_query is None


def test_filter_strategies_treat_none_as_empty() -> None:
    """Every filter strategy returns an empty `FilterEncoding` for `None`."""
    empty = FilterEncoding()
    assert KeyValueFilter().encode(None) == empty
    assert KeyOpValueFilter().encode(None) == empty
    assert SearchFilterStrategy().encode(None) == empty
    assert JsonFilterStrategy().encode(None) == empty
    # Empty encoding is falsy so iterators / count() can short-circuit cheaply.
    assert not empty


def test_filter_encoding_is_truthy_when_either_field_populated() -> None:
    """`FilterEncoding` is truthy when params or raw_query carry content."""
    assert FilterEncoding(params={"a": 1})
    assert FilterEncoding(raw_query="and(eq(a,1))")
    assert not FilterEncoding()


def test_user_filter_strategy_can_emit_raw_query_fragment() -> None:
    """A user-defined strategy can emit `raw_query` for RQL-style dialects.

    Verifies the public contract: returning a `FilterEncoding(raw_query=...)`
    is the supported way to pass an expression that must be appended verbatim
    to the URL's query string instead of going through httpx `params=` (which
    would URL-encode parentheses and split on commas).
    """

    class RqlLike:
        def encode(self, f: Filter | None) -> FilterEncoding:
            if f is None:
                return FilterEncoding()
            terms = [f"eq({k},{v})" for k, v in f.kwargs.items()]
            return FilterEncoding(raw_query="and(" + ",".join(terms) + ")")

    encoding = RqlLike().encode(Filter(field1="value1", field2="value2"))

    assert encoding.params == {}
    assert encoding.raw_query == "and(eq(field1,value1),eq(field2,value2))"


# --------------------------------------------------------------------------- #
# Sort                                                                        #
# --------------------------------------------------------------------------- #


def test_comma_signed_sort_renders_signed_csv() -> None:
    """`CommaSignedSort` joins terms with commas; descending gets a leading `-`."""
    expr = Sort("-created_at") + Sort("id")

    encoding = CommaSignedSort(param="sort").encode(expr)

    assert encoding == SortEncoding(params={"sort": "-created_at,id"})
    assert encoding.raw_query is None


def test_key_direction_sort_emits_two_params() -> None:
    """`KeyDirectionSort` emits separate field/direction parameters."""
    expr = Sort("-created_at")

    encoding = KeyDirectionSort().encode(expr)

    assert encoding.params == {"order_by": "created_at", "order": "desc"}
    assert encoding.raw_query is None


def test_key_direction_sort_refuses_multi_term() -> None:
    """`KeyDirectionSort` cannot represent multiple sort fields."""
    expr = Sort("a") + Sort("b")

    with pytest.raises(UnsupportedSortError):
        KeyDirectionSort().encode(expr)


def test_json_api_sort_matches_jsonapi_format() -> None:
    """`JsonApiSort` matches the JSON:API sort encoding (signed, comma-joined)."""
    expr = Sort("-created_at") + Sort("name")

    encoding = JsonApiSort().encode(expr)

    assert encoding.params == {"sort": "-created_at,name"}
    assert encoding.raw_query is None


def test_sort_strategies_treat_empty_sort_as_no_op() -> None:
    """An empty `Sort()` (no terms) yields an empty `SortEncoding` from every strategy."""
    empty = SortEncoding()
    assert CommaSignedSort().encode(None) == empty
    assert CommaSignedSort().encode(Sort()) == empty
    assert KeyDirectionSort().encode(None) == empty
    assert JsonApiSort().encode(Sort()) == empty
    # Empty encoding is falsy so iterators / count() can short-circuit cheaply.
    assert not empty


def test_sort_encoding_is_truthy_when_either_field_populated() -> None:
    """`SortEncoding` is truthy when params or raw_query carry content."""
    assert SortEncoding(params={"sort": "name"})
    assert SortEncoding(raw_query="ordering(+name)")
    assert not SortEncoding()


def test_user_sort_strategy_can_emit_raw_query_fragment() -> None:
    """A user-defined sort strategy can emit `raw_query` for RQL-style dialects.

    Verifies the public contract: returning a `SortEncoding(raw_query=...)`
    is the supported way to pass a sort expression that must be appended
    verbatim to the URL's query string instead of going through httpx
    `params=` (which would URL-encode parentheses and split on commas).
    """

    class RqlOrdering:
        def encode(self, s: Sort | None) -> SortEncoding:
            if not s:
                return SortEncoding()
            terms = [
                f"-{field_}" if direction == "desc" else f"+{field_}"
                for field_, direction in s.terms
            ]
            return SortEncoding(raw_query="ordering(" + ",".join(terms) + ")")

    encoding = RqlOrdering().encode(Sort("-created_at") + Sort("name"))

    assert encoding.params == {}
    assert encoding.raw_query == "ordering(-created_at,+name)"
