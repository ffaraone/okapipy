"""Runtime library vendored into every generated client package.

These modules are copied verbatim into generated packages by
`okapipy.generator.emit.runtime`. They are also importable here so okapipy's own
test suite can exercise them directly. Imports inside this package are relative
so the same files work after vendoring under any package name.
"""

from __future__ import annotations

from .exceptions import (
    ApiError,
    ClientError,
    ConfigurationError,
    ResponseValidationError,
    ServerError,
    UnsupportedFilterError,
    UnsupportedFilterKeyError,
    UnsupportedSortError,
    UnsupportedSortFieldError,
)
from .filters import AndFilter, Filter, NotFilter, OrFilter, Search
from .sort import Sort
from .strategies import (
    CommaSignedSort,
    CursorPagination,
    FilterStrategy,
    JsonApiSort,
    JsonFilterStrategy,
    KeyDirectionSort,
    KeyOpValueFilter,
    KeyValueFilter,
    LimitOffsetPagination,
    LinkHeaderPagination,
    PageNumberPagination,
    PaginationStrategy,
    SearchFilterStrategy,
    SortStrategy,
)
from .transport import RetryPolicy, RetryTransport
from .types import UNSET, RequestOptions, Unset

__all__ = [
    "UNSET",
    "AndFilter",
    "ApiError",
    "ClientError",
    "CommaSignedSort",
    "ConfigurationError",
    "CursorPagination",
    "Filter",
    "FilterStrategy",
    "JsonApiSort",
    "JsonFilterStrategy",
    "KeyDirectionSort",
    "KeyOpValueFilter",
    "KeyValueFilter",
    "LinkHeaderPagination",
    "NotFilter",
    "LimitOffsetPagination",
    "OrFilter",
    "PageNumberPagination",
    "PaginationStrategy",
    "RequestOptions",
    "ResponseValidationError",
    "RetryPolicy",
    "RetryTransport",
    "Search",
    "SearchFilterStrategy",
    "ServerError",
    "Sort",
    "SortStrategy",
    "Unset",
    "UnsupportedFilterError",
    "UnsupportedFilterKeyError",
    "UnsupportedSortError",
    "UnsupportedSortFieldError",
]
