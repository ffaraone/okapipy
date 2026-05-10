"""Pagination, filter, and sort strategies — Protocols and built-in implementations.

Strategies are stateless translators. Pagination strategies drive the iterator
(`initial`, `next`, `extract_items`) and optionally produce count requests
(`supports_count`, `count_request_params`, `extract_count`). Filter and sort
strategies walk the user's `Filter` / `Sort` tree and produce wire parameters.

Strategies are duck-typed against the Protocols below — users do not have to
inherit from them.

Pagination strategies own their default page size: each built-in *requires* a
`default_page_size` argument that seeds `initial(...)` when the per-call
`page_size` is `None`. The client no longer holds a `default_page_size` — it
is a property of the wire dialect, which is exactly what the strategy models.
Making it required (rather than `int | None`) is deliberate: a `None` would
fall back to whatever default the backend chooses, leaving the client unable
to tell what page size each request actually carries. Users can still
override per-call via `.page_size(n)`.

Filter and sort strategies return `FilterEncoding` / `SortEncoding` objects
rather than bare params dicts. Each encoding carries both ordinary key/value
`params` and an optional `raw_query` fragment for dialects whose expression
must be emitted verbatim into the query string instead of as `key=value`
pairs (e.g. RQL filters: `?and(eq(f1,v1),eq(f2,v2))`, or RQL-style sort:
`?ordering(+name,-created_at)`). When both filter and sort produce raw
fragments they are concatenated into the URL with `&` separators alongside
the URL-encoded ordinary params (e.g. `?and(eq(f,v))&ordering(+name)&limit=100`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from .exceptions import (
    ConfigurationError,
    UnsupportedFilterError,
    UnsupportedPaginationError,
    UnsupportedSortError,
)
from .filters import AndFilter, Filter, NotFilter, OrFilter, Search
from .sort import Sort


@dataclass(frozen=True)
class FilterEncoding:
    """The wire form of a `Filter` tree as produced by a `FilterStrategy`.

    `params` are merged into the request's `params=` argument (key/value pairs
    that httpx URL-encodes). `raw_query` is a query-string fragment appended
    verbatim — required for expression-language filters like RQL where the
    operators (`and(...)`, `eq(...)`) carry parentheses and commas that must
    not be split into separate parameters.
    """

    params: Mapping[str, Any] = field(default_factory=dict)
    raw_query: str | None = None

    def __bool__(self) -> bool:
        return bool(self.params) or bool(self.raw_query)


@dataclass(frozen=True)
class SortEncoding:
    """The wire form of a `Sort` term list as produced by a `SortStrategy`.

    Mirrors `FilterEncoding`. `params` are merged into the request's `params=`
    argument; `raw_query` is a query-string fragment appended verbatim — for
    sort dialects whose expression must not be URL-encoded as `key=value`
    pairs (e.g. an RQL-style `ordering(+name,-created_at)` term).
    """

    params: Mapping[str, Any] = field(default_factory=dict)
    raw_query: str | None = None

    def __bool__(self) -> bool:
        return bool(self.params) or bool(self.raw_query)


@runtime_checkable
class PaginationStrategy(Protocol):
    """Drives the iterator and (optionally) the count and random-access requests.

    `supports_count` / `count_request_params` / `extract_count` form the count
    capability. `supports_random_access` / `page_params` form the random-access
    capability — the ability to fetch the N-th page directly without walking
    pages 0..N-1. Cursor and link-header paginations are inherently sequential
    and report `supports_random_access=False`; offset and page-number
    paginations support it.
    """

    @property
    def supports_count(self) -> bool: ...

    @property
    def supports_random_access(self) -> bool: ...

    def initial(self, page_size: int | None) -> Mapping[str, Any]: ...

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None: ...

    def extract_items(self, response: httpx.Response) -> list[Any]: ...

    def count_request_params(
        self, base_params: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def extract_count(self, response: httpx.Response) -> int: ...

    def page_params(
        self, page_num: int, page_size: int | None
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class FilterStrategy(Protocol):
    """Renders a `Filter` tree into a `FilterEncoding`."""

    def encode(self, f: Filter | None) -> FilterEncoding: ...


@runtime_checkable
class SortStrategy(Protocol):
    """Renders a `Sort` term list into a `SortEncoding`."""

    def encode(self, s: Sort | None) -> SortEncoding: ...


# --------------------------------------------------------------------------- #
# Pagination built-ins                                                        #
# --------------------------------------------------------------------------- #

ITEM_KEYS = ("items", "data", "results")


def _extract_items_from_envelope(response: httpx.Response) -> list[Any]:
    """Default item extraction: top-level array, or `items`/`data`/`results` field."""
    body = response.json()
    if isinstance(body, list):
        return list(body)
    if isinstance(body, dict):
        for key in ITEM_KEYS:
            value = body.get(key)
            if isinstance(value, list):
                return list(value)
    return []


def _parse_content_range_total(value: str) -> int | None:
    """Parse `Content-Range: items 0-49/1234` and return the total (`1234`).

    Returns `None` when the header is absent, malformed, or carries `*` for the
    total (per RFC 7233).
    """
    if "/" not in value:
        return None
    total = value.rsplit("/", 1)[-1].strip()
    return int(total) if total.isdigit() else None


def _read_dotted_path(body: Any, path: str) -> Any:
    """Walk a dotted path through nested mappings; return `None` on miss.

    `path="total"` reads `body["total"]`; `path="meta.pagination.total"` reads
    `body["meta"]["pagination"]["total"]`. Returns `None` if any intermediate
    key is missing or any intermediate value is not a mapping — callers should
    treat that as "no total available" rather than raising.
    """
    current: Any = body
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


@dataclass
class LimitOffsetPagination:
    """`?offset=&limit=` pagination with configurable count source.

    `default_page_size` is required — it seeds `limit` when the caller did not
    invoke `.page_size(...)`. The per-call value passed to `initial` always
    wins when present.

    `total_field` accepts a dotted path for envelopes that nest the total
    (e.g. `"meta.pagination.total"` reads `body["meta"]["pagination"]["total"]`).
    """

    default_page_size: int
    offset_param: str = "offset"
    limit_param: str = "limit"
    total_field: str | None = "total"
    total_header: str | None = None
    content_range: bool = False

    @property
    def supports_count(self) -> bool:
        return (
            self.total_field is not None
            or self.total_header is not None
            or self.content_range
        )

    @property
    def supports_random_access(self) -> bool:
        return True

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        return {
            self.offset_param: 0,
            self.limit_param: page_size
            if page_size is not None
            else self.default_page_size,
        }

    def page_params(self, page_num: int, page_size: int | None) -> Mapping[str, Any]:
        if page_num < 0:
            raise ValueError(f"page_num must be non-negative; got {page_num}")
        size = page_size if page_size is not None else self.default_page_size
        return {self.offset_param: page_num * size, self.limit_param: size}

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        items = self.extract_items(response)
        if not items:
            return None
        last_offset = int(last_params.get(self.offset_param, 0) or 0)
        last_limit = int(last_params.get(self.limit_param, len(items)) or len(items))
        new_offset = last_offset + last_limit
        total = self._read_total(response)
        if total is not None and new_offset >= total:
            return None
        return {**last_params, self.offset_param: new_offset}

    def extract_items(self, response: httpx.Response) -> list[Any]:
        return _extract_items_from_envelope(response)

    def count_request_params(self, base_params: Mapping[str, Any]) -> Mapping[str, Any]:
        params = dict(base_params)
        params[self.offset_param] = 0
        params[self.limit_param] = 1
        return params

    def extract_count(self, response: httpx.Response) -> int:
        total = self._read_total(response)
        if total is None:
            raise ConfigurationError(
                "LimitOffsetPagination has no count source configured"
            )
        return total

    def _read_total(self, response: httpx.Response) -> int | None:
        if self.total_field is not None:
            value = _read_dotted_path(response.json(), self.total_field)
            if value is not None:
                return int(value)
        if self.total_header is not None:
            value = response.headers.get(self.total_header)
            if value is not None and value.isdigit():
                return int(value)
        if self.content_range:
            cr = response.headers.get("Content-Range")
            if cr is not None:
                return _parse_content_range_total(cr)
        return None


@dataclass
class PageNumberPagination:
    """`?page=&page_size=` pagination.

    `total_field` accepts a dotted path for envelopes that nest the total
    (e.g. `"meta.pagination.total"` reads `body["meta"]["pagination"]["total"]`).
    """

    default_page_size: int
    page_param: str = "page"
    page_size_param: str = "page_size"
    start_page: int = 1
    total_field: str | None = None
    total_header: str | None = None

    @property
    def supports_count(self) -> bool:
        return self.total_field is not None or self.total_header is not None

    @property
    def supports_random_access(self) -> bool:
        return True

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        return {
            self.page_param: self.start_page,
            self.page_size_param: page_size
            if page_size is not None
            else self.default_page_size,
        }

    def page_params(self, page_num: int, page_size: int | None) -> Mapping[str, Any]:
        if page_num < 0:
            raise ValueError(f"page_num must be non-negative; got {page_num}")
        return {
            self.page_param: self.start_page + page_num,
            self.page_size_param: page_size
            if page_size is not None
            else self.default_page_size,
        }

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        items = self.extract_items(response)
        if not items:
            return None
        last_page = int(last_params.get(self.page_param, self.start_page))
        last_size = last_params.get(self.page_size_param)
        if last_size is not None and len(items) < int(last_size):
            return None
        return {**last_params, self.page_param: last_page + 1}

    def extract_items(self, response: httpx.Response) -> list[Any]:
        return _extract_items_from_envelope(response)

    def count_request_params(self, base_params: Mapping[str, Any]) -> Mapping[str, Any]:
        params = dict(base_params)
        params[self.page_param] = self.start_page
        params[self.page_size_param] = 1
        return params

    def extract_count(self, response: httpx.Response) -> int:
        if self.total_field is not None:
            value = _read_dotted_path(response.json(), self.total_field)
            if value is not None:
                return int(value)
        if self.total_header is not None:
            value = response.headers.get(self.total_header)
            if value is not None and value.isdigit():
                return int(value)
        raise ConfigurationError("PageNumberPagination has no count source configured")


@dataclass
class CursorPagination:
    """Opaque-cursor pagination: `?cursor=...` + a next-cursor field in the response.

    `page_size_param=None` opts out of sending any size hint at all; otherwise
    `default_page_size` is used when the caller did not invoke `.page_size(...)`.
    """

    default_page_size: int
    cursor_param: str = "cursor"
    next_cursor_field: str = "next_cursor"
    page_size_param: str | None = "page_size"
    content_range: bool = False

    @property
    def supports_count(self) -> bool:
        return self.content_range

    @property
    def supports_random_access(self) -> bool:
        return False

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        if self.page_size_param is None:
            return {}
        return {
            self.page_size_param: page_size
            if page_size is not None
            else self.default_page_size,
        }

    def page_params(self, page_num: int, page_size: int | None) -> Mapping[str, Any]:
        raise UnsupportedPaginationError(
            "CursorPagination is sequential — fetching page N requires the "
            "cursor returned by page N-1; iterate the collection instead"
        )

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        body = response.json()
        if not isinstance(body, dict):
            return None
        token = body.get(self.next_cursor_field)
        if not token:
            return None
        return {**last_params, self.cursor_param: token}

    def extract_items(self, response: httpx.Response) -> list[Any]:
        return _extract_items_from_envelope(response)

    def count_request_params(self, base_params: Mapping[str, Any]) -> Mapping[str, Any]:
        params = dict(base_params)
        if self.page_size_param is not None:
            params[self.page_size_param] = 1
        return params

    def extract_count(self, response: httpx.Response) -> int:
        if self.content_range:
            cr = response.headers.get("Content-Range")
            if cr is not None:
                total = _parse_content_range_total(cr)
                if total is not None:
                    return total
        raise ConfigurationError(
            "CursorPagination supports count only when configured with content_range=True"
        )


@dataclass
class LinkHeaderPagination:
    """RFC 5988 `Link: <…>; rel="next"` pagination.

    `page_size_param=None` opts out of sending any size hint at all; otherwise
    `default_page_size` is used when the caller did not invoke `.page_size(...)`.
    """

    default_page_size: int
    page_size_param: str | None = "limit"
    content_range: bool = True
    total_header: str | None = "X-Total-Count"

    @property
    def supports_count(self) -> bool:
        return self.content_range or self.total_header is not None

    @property
    def supports_random_access(self) -> bool:
        return False

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        if self.page_size_param is None:
            return {}
        return {
            self.page_size_param: page_size
            if page_size is not None
            else self.default_page_size,
        }

    def page_params(self, page_num: int, page_size: int | None) -> Mapping[str, Any]:
        raise UnsupportedPaginationError(
            "LinkHeaderPagination is sequential — page N is reachable only by "
            'following the rel="next" link returned by page N-1; iterate the '
            "collection instead"
        )

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        next_url = self._extract_next_link(response.headers.get("Link"))
        if next_url is None:
            return None
        # The next link carries its own query string; we surface it as a special
        # marker the iterator will inspect (`__url__`) so the request goes to that
        # URL verbatim instead of using the original collection path.
        return {"__url__": next_url}

    def extract_items(self, response: httpx.Response) -> list[Any]:
        return _extract_items_from_envelope(response)

    def count_request_params(self, base_params: Mapping[str, Any]) -> Mapping[str, Any]:
        params = dict(base_params)
        if self.page_size_param is not None:
            params[self.page_size_param] = 1
        return params

    def extract_count(self, response: httpx.Response) -> int:
        if self.content_range:
            cr = response.headers.get("Content-Range")
            if cr is not None:
                total = _parse_content_range_total(cr)
                if total is not None:
                    return total
        if self.total_header is not None:
            value = response.headers.get(self.total_header)
            if value is not None and value.isdigit():
                return int(value)
        raise ConfigurationError("LinkHeaderPagination has no count source configured")

    def _extract_next_link(self, header_value: str | None) -> str | None:
        if not header_value:
            return None
        for entry in header_value.split(","):
            parts = entry.strip().split(";")
            if len(parts) < 2:
                continue
            url_part = parts[0].strip()
            rel_parts = [p.strip() for p in parts[1:]]
            if not url_part.startswith("<") or not url_part.endswith(">"):
                continue
            if any(rp.replace(" ", "") == 'rel="next"' for rp in rel_parts):
                return url_part[1:-1]
        return None


# --------------------------------------------------------------------------- #
# Filter built-ins                                                            #
# --------------------------------------------------------------------------- #


def _and_leaves(node: Filter, label: str) -> list[Filter]:
    """Flatten `&`-composed leaves; raise on `|`/`~` because `label` rejects them."""
    if isinstance(node, AndFilter):
        return [*_and_leaves(node.left, label), *_and_leaves(node.right, label)]
    if isinstance(node, (OrFilter, NotFilter)):
        raise UnsupportedFilterError(
            f"{label} accepts conjunctive expressions only (no OR/NOT)"
        )
    return [node]


@dataclass
class KeyValueFilter:
    """Equality-only conjunctive filter: `?status=open&customer_id=42`."""

    def encode(self, f: Filter | None) -> FilterEncoding:
        if f is None:
            return FilterEncoding()
        params: dict[str, Any] = {}
        for leaf in _and_leaves(f, "KeyValueFilter"):
            if isinstance(leaf, Search):
                raise UnsupportedFilterError(
                    "KeyValueFilter does not support Search leaves"
                )
            for key, value in leaf.kwargs.items():
                if "__" in key:
                    raise UnsupportedFilterError(
                        f"KeyValueFilter does not support operator suffix: {key!r}"
                    )
                params[key] = value
        return FilterEncoding(params=params)


@dataclass
class KeyOpValueFilter:
    """Django-style operator-suffix filter: `?created_at__gte=…&status__in=…`."""

    def encode(self, f: Filter | None) -> FilterEncoding:
        if f is None:
            return FilterEncoding()
        params: dict[str, Any] = {}
        for leaf in _and_leaves(f, "KeyOpValueFilter"):
            if isinstance(leaf, Search):
                raise UnsupportedFilterError(
                    "KeyOpValueFilter does not support Search leaves"
                )
            for key, value in leaf.kwargs.items():
                params[key] = value
        return FilterEncoding(params=params)


@dataclass
class SearchFilterStrategy:
    """Single free-text param filter: `?q=…`. Accepts only one `Search` leaf."""

    param: str = "q"

    def encode(self, f: Filter | None) -> FilterEncoding:
        if f is None:
            return FilterEncoding()
        leaves = list(f.iter_leaves())
        if len(leaves) != 1 or not isinstance(leaves[0], Search):
            raise UnsupportedFilterError(
                "SearchFilterStrategy accepts a single Search(...) leaf only"
            )
        return FilterEncoding(params={self.param: leaves[0].query})


@dataclass
class JsonFilterStrategy:
    """Encode the full `Filter` tree as a JSON expression in one query parameter."""

    param: str = "filter"

    def encode(self, f: Filter | None) -> FilterEncoding:
        if f is None:
            return FilterEncoding()
        return FilterEncoding(params={self.param: json.dumps(self._to_json(f))})

    def _to_json(self, node: Filter) -> Any:
        if isinstance(node, AndFilter):
            return {"and": [self._to_json(node.left), self._to_json(node.right)]}
        if isinstance(node, OrFilter):
            return {"or": [self._to_json(node.left), self._to_json(node.right)]}
        if isinstance(node, NotFilter):
            return {"not": self._to_json(node.operand)}
        if isinstance(node, Search):
            return {"q": node.query}
        return dict(node.kwargs)


# --------------------------------------------------------------------------- #
# Sort built-ins                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class CommaSignedSort:
    """`?sort=-created_at,id` — comma-joined, leading `-` for desc."""

    param: str = "sort"

    def encode(self, s: Sort | None) -> SortEncoding:
        if not s:
            return SortEncoding()
        rendered = ",".join(
            f"-{field_}" if direction == "desc" else field_
            for field_, direction in s.terms
        )
        return SortEncoding(params={self.param: rendered})


@dataclass
class KeyDirectionSort:
    """`?order_by=created_at&order=desc` — separate field/direction params.

    Only single-term sort is meaningful for this encoding; multi-term raises.
    """

    field_param: str = "order_by"
    direction_param: str = "order"

    def encode(self, s: Sort | None) -> SortEncoding:
        if not s:
            return SortEncoding()
        if len(s.terms) > 1:
            raise UnsupportedSortError(
                "KeyDirectionSort encodes a single field only; got "
                f"{len(s.terms)} terms"
            )
        field_, direction = s.terms[0]
        return SortEncoding(
            params={self.field_param: field_, self.direction_param: direction}
        )


@dataclass
class JsonApiSort:
    """JSON:API sort encoding (`?sort=-created_at,id`).

    Identical wire format to `CommaSignedSort` with `param='sort'`; provided as a
    distinct class so user code can document the API style explicitly.
    """

    param: str = "sort"

    def encode(self, s: Sort | None) -> SortEncoding:
        if not s:
            return SortEncoding()
        rendered = ",".join(
            f"-{field_}" if direction == "desc" else field_
            for field_, direction in s.terms
        )
        return SortEncoding(params={self.param: rendered})


# Stops mypy `unused import` complaints; `field` is used to make the dataclasses
# above nicely typed in inheritance scenarios.
_ = field
