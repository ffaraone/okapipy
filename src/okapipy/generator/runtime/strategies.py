"""Pagination, filter, and sort strategies — Protocols and built-in implementations.

Strategies are stateless translators. Pagination strategies drive the iterator
(`initial`, `next`, `extract_items`) and optionally produce count requests
(`supports_count`, `count_request_params`, `extract_count`). Filter and sort
strategies walk the user's `Filter` / `Sort` tree and produce wire parameters.

Strategies are duck-typed against the Protocols below — users do not have to
inherit from them.
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
    UnsupportedSortError,
)
from .filters import AndFilter, Filter, NotFilter, OrFilter, Search
from .sort import Sort


@runtime_checkable
class PaginationStrategy(Protocol):
    """Drives the iterator and (optionally) the count request."""

    @property
    def supports_count(self) -> bool: ...

    def initial(self, page_size: int | None) -> Mapping[str, Any]: ...

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None: ...

    def extract_items(self, response: httpx.Response) -> list[Any]: ...

    def count_request_params(
        self, base_params: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def extract_count(self, response: httpx.Response) -> int: ...


@runtime_checkable
class FilterStrategy(Protocol):
    """Renders a `Filter` tree into request parameters."""

    def encode(self, f: Filter | None) -> Mapping[str, Any]: ...


@runtime_checkable
class SortStrategy(Protocol):
    """Renders a `Sort` term list into request parameters."""

    def encode(self, s: Sort | None) -> Mapping[str, Any]: ...


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


@dataclass
class OffsetLimitPagination:
    """`?offset=&limit=` pagination with configurable count source."""

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

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        params: dict[str, Any] = {self.offset_param: 0}
        if page_size is not None:
            params[self.limit_param] = page_size
        return params

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
                "OffsetLimitPagination has no count source configured"
            )
        return total

    def _read_total(self, response: httpx.Response) -> int | None:
        if self.total_field is not None:
            body = response.json()
            if isinstance(body, dict) and self.total_field in body:
                return int(body[self.total_field])
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
    """`?page=&page_size=` pagination."""

    page_param: str = "page"
    page_size_param: str = "page_size"
    start_page: int = 1
    total_field: str | None = None
    total_header: str | None = None

    @property
    def supports_count(self) -> bool:
        return self.total_field is not None or self.total_header is not None

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        params: dict[str, Any] = {self.page_param: self.start_page}
        if page_size is not None:
            params[self.page_size_param] = page_size
        return params

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
            body = response.json()
            if isinstance(body, dict) and self.total_field in body:
                return int(body[self.total_field])
        if self.total_header is not None:
            value = response.headers.get(self.total_header)
            if value is not None and value.isdigit():
                return int(value)
        raise ConfigurationError("PageNumberPagination has no count source configured")


@dataclass
class CursorPagination:
    """Opaque-cursor pagination: `?cursor=...` + a next-cursor field in the response."""

    cursor_param: str = "cursor"
    next_cursor_field: str = "next_cursor"
    page_size_param: str | None = "page_size"
    content_range: bool = False

    @property
    def supports_count(self) -> bool:
        return self.content_range

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        params: dict[str, Any] = {}
        if page_size is not None and self.page_size_param is not None:
            params[self.page_size_param] = page_size
        return params

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
    """RFC 5988 `Link: <…>; rel="next"` pagination."""

    page_size_param: str | None = "limit"
    content_range: bool = True
    total_header: str | None = "X-Total-Count"

    @property
    def supports_count(self) -> bool:
        return self.content_range or self.total_header is not None

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        params: dict[str, Any] = {}
        if page_size is not None and self.page_size_param is not None:
            params[self.page_size_param] = page_size
        return params

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


@dataclass
class KeyValueFilter:
    """Equality-only conjunctive filter: `?status=open&customer_id=42`."""

    def encode(self, f: Filter | None) -> Mapping[str, Any]:
        if f is None:
            return {}
        params: dict[str, Any] = {}
        for leaf in self._iter_and_leaves(f):
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
        return params

    def _iter_and_leaves(self, node: Filter) -> list[Filter]:
        if isinstance(node, AndFilter):
            return [
                *self._iter_and_leaves(node.left),
                *self._iter_and_leaves(node.right),
            ]
        if isinstance(node, (OrFilter, NotFilter)):
            raise UnsupportedFilterError(
                "KeyValueFilter accepts conjunctive equality only (no OR/NOT)"
            )
        return [node]


@dataclass
class KeyOpValueFilter:
    """Django-style operator-suffix filter: `?created_at__gte=…&status__in=…`."""

    def encode(self, f: Filter | None) -> Mapping[str, Any]:
        if f is None:
            return {}
        params: dict[str, Any] = {}
        for leaf in self._iter_and_leaves(f):
            if isinstance(leaf, Search):
                raise UnsupportedFilterError(
                    "KeyOpValueFilter does not support Search leaves"
                )
            for key, value in leaf.kwargs.items():
                params[key] = value
        return params

    def _iter_and_leaves(self, node: Filter) -> list[Filter]:
        if isinstance(node, AndFilter):
            return [
                *self._iter_and_leaves(node.left),
                *self._iter_and_leaves(node.right),
            ]
        if isinstance(node, (OrFilter, NotFilter)):
            raise UnsupportedFilterError(
                "KeyOpValueFilter accepts conjunctive expressions only (no OR/NOT)"
            )
        return [node]


@dataclass
class SearchFilterStrategy:
    """Single free-text param filter: `?q=…`. Accepts only one `Search` leaf."""

    param: str = "q"

    def encode(self, f: Filter | None) -> Mapping[str, Any]:
        if f is None:
            return {}
        leaves = list(f.iter_leaves())
        if len(leaves) != 1 or not isinstance(leaves[0], Search):
            raise UnsupportedFilterError(
                "SearchFilterStrategy accepts a single Search(...) leaf only"
            )
        return {self.param: leaves[0].query}


@dataclass
class JsonFilterStrategy:
    """Encode the full `Filter` tree as a JSON expression in one query parameter."""

    param: str = "filter"

    def encode(self, f: Filter | None) -> Mapping[str, Any]:
        if f is None:
            return {}
        return {self.param: json.dumps(self._to_json(f))}

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

    def encode(self, s: Sort | None) -> Mapping[str, Any]:
        if not s:
            return {}
        rendered = ",".join(
            f"-{field_}" if direction == "desc" else field_
            for field_, direction in s.terms
        )
        return {self.param: rendered}


@dataclass
class KeyDirectionSort:
    """`?order_by=created_at&order=desc` — separate field/direction params.

    Only single-term sort is meaningful for this encoding; multi-term raises.
    """

    field_param: str = "order_by"
    direction_param: str = "order"

    def encode(self, s: Sort | None) -> Mapping[str, Any]:
        if not s:
            return {}
        if len(s.terms) > 1:
            raise UnsupportedSortError(
                "KeyDirectionSort encodes a single field only; got "
                f"{len(s.terms)} terms"
            )
        field_, direction = s.terms[0]
        return {self.field_param: field_, self.direction_param: direction}


@dataclass
class JsonApiSort:
    """JSON:API sort encoding (`?sort=-created_at,id`).

    Identical wire format to `CommaSignedSort` with `param='sort'`; provided as a
    distinct class so user code can document the API style explicitly.
    """

    param: str = "sort"

    def encode(self, s: Sort | None) -> Mapping[str, Any]:
        if not s:
            return {}
        rendered = ",".join(
            f"-{field_}" if direction == "desc" else field_
            for field_, direction in s.terms
        )
        return {self.param: rendered}


# Stops mypy `unused import` complaints; `field` is used to make the dataclasses
# above nicely typed in inheritance scenarios.
_ = field
