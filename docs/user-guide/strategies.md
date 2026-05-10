# Strategies

A **strategy** is a small object that tells the generated client how a
specific concern is encoded *on the wire*. okapipy ships strategies for
three concerns: **pagination**, **filter**, and **sort**.

## Why strategies?

OpenAPI says nothing about how an API paginates a list, what filter
syntax the query string uses, or how sort terms are encoded. Real APIs
have opinions: offset/limit vs. page-number vs. opaque cursors,
`?status=open` vs. `?status__eq=open` vs. RQL, JSON:API's
`?sort=-created_at,id` vs. two-parameter
`?order_by=created_at&order=desc`.

Rather than try to infer this from the spec (we'd guess wrong half the
time), okapipy moves the choice to **runtime configuration**. You hand
your client the strategies that match your API; the generated code calls
them through three small Protocols.

## Where the strategies live

Once a client is generated, every strategy class is importable from
`<your_package>.base.strategies`. The Protocols, the built-ins, and the
`FilterEncoding` value type are all there:

```python
from acme.commerce.base.strategies import (
    PaginationStrategy, FilterStrategy, SortStrategy,
    FilterEncoding, SortEncoding,
    LimitOffsetPagination, PageNumberPagination, CursorPagination, LinkHeaderPagination,
    KeyValueFilter, KeyOpValueFilter, SearchFilterStrategy, JsonFilterStrategy,
    CommaSignedSort, KeyDirectionSort, JsonApiSort,
)
```

The `Filter` and `Sort` DSL types live next door:

```python
from acme.commerce.base.filters import Filter, Search   # AndFilter, OrFilter, NotFilter for advanced cases
from acme.commerce.base.sort import Sort
```

## The Protocols

```python
class PaginationStrategy(Protocol):
    @property
    def supports_count(self) -> bool: ...
    @property
    def supports_random_access(self) -> bool: ...
    def initial(self, page_size: int | None) -> Mapping[str, Any]: ...
    def next(self, response, last_params) -> Mapping[str, Any] | None: ...
    def extract_items(self, response) -> list[Any]: ...
    def count_request_params(self, base_params) -> Mapping[str, Any]: ...
    def extract_count(self, response) -> int: ...
    def page_params(self, page_num: int, page_size: int | None) -> Mapping[str, Any]: ...

class FilterStrategy(Protocol):
    def encode(self, f: Filter | None) -> FilterEncoding: ...

class SortStrategy(Protocol):
    def encode(self, s: Sort | None) -> SortEncoding: ...
```

All three are `runtime_checkable` and **duck-typed**. Your strategy
class does **not** need to inherit from them — it just needs the right
shape. A `@dataclass` is the conventional form for built-ins.

## Defaults

If you don't pass strategies into the client constructor, you get:

```python
pagination_strategy = LimitOffsetPagination(default_page_size=100)
filter_strategy     = KeyValueFilter()
sort_strategy       = CommaSignedSort()
```



That's a reasonable starting point for most flat-shaped REST APIs. Read
on for when you need something else.

## Built-in pagination strategies

Every pagination built-in **requires a `default_page_size`**. That is
deliberate: a `None` would let the backend choose, and the client would
have no way to tell what page size each request actually carries.
Per-call `.page_size(n)` always wins over the default.

| Strategy | Wire form | Count source | Random page access |
| --- | --- | --- | --- |
| `LimitOffsetPagination` | `?offset=0&limit=50` | `total_field` (dotted path), `total_header`, or `Content-Range` | Yes |
| `PageNumberPagination` | `?page=1&page_size=50` | `total_field` (dotted path) or `total_header` | Yes |
| `CursorPagination` | `?cursor=<opaque>&page_size=50` | `Content-Range` (opt-in) | No (sequential) |
| `LinkHeaderPagination` | RFC 5988 `Link: <…>; rel="next"` | `Content-Range` or `total_header` | No (sequential) |

Random page access is what the collection's
[`get_page(page_num)`](client-usage.md#fetch-a-single-page-directly)
needs — the ability to compute the params for the N-th page directly,
without consuming the N-1 pages before it. Offset and page-number
strategies have it (offset = `page_num * size`; page = `start_page +
page_num`). Cursor and link-header strategies are inherently sequential:
the server hands you each next token alongside the current page, and
there is no way to manufacture page N's token without page N-1's
response. They report `supports_random_access=False` and reject
`get_page` with `UnsupportedPaginationError`.

```python
from acme.commerce.base.strategies import (
    LimitOffsetPagination,
    PageNumberPagination,
    CursorPagination,
    LinkHeaderPagination,
)

# Plain offset/limit, total in body.
LimitOffsetPagination(default_page_size=50)

# Total nested under an envelope (dotted path).
LimitOffsetPagination(default_page_size=50, total_field="meta.pagination.total")

# Total in a header instead.
LimitOffsetPagination(
    default_page_size=50, total_field=None, total_header="X-Total-Count",
)

# Page-number, JSON:API-ish.
PageNumberPagination(
    default_page_size=25,
    page_param="page[number]",
    page_size_param="page[size]",
)

# Cursor, no size hint at all.
CursorPagination(
    default_page_size=100,
    page_size_param=None,
    next_cursor_field="meta.next_cursor",
)

# Stripe-ish link header.
LinkHeaderPagination(default_page_size=100)
```

## Built-in filter strategies

| Strategy | Wire form | Notes |
| --- | --- | --- |
| `KeyValueFilter` | `?status=open&customer_id=42` | Equality only; AND-of-leaves only. |
| `KeyOpValueFilter` | `?created_at__gte=…&status__in=…` | Django-style operator suffix; AND only. |
| `SearchFilterStrategy` | `?q=…` | Single free-text leaf (`Search("...")`). |
| `JsonFilterStrategy` | `?filter=<json>` | Full Filter tree as a JSON expression in one query parameter. |

Filter strategies return a `FilterEncoding`, which carries both ordinary
key/value `params` and an optional `raw_query` fragment. The latter is
required for expression-language filters (RQL, for example) where the
operators carry parentheses and commas that mustn't be split into
separate parameters.

When you compose a `Filter` tree the configured strategy can't encode
(an OR with `KeyValueFilter`, a `Search` with `KeyOpValueFilter`,
multiple leaves with `SearchFilterStrategy`), the strategy raises
`UnsupportedFilterError` from `<package>.base.exceptions`.

## Built-in sort strategies

| Strategy | Wire form | Multi-term? |
| --- | --- | --- |
| `CommaSignedSort` | `?sort=-created_at,id` | Yes |
| `JsonApiSort` | `?sort=-created_at,id` | Yes (alias for the JSON:API style) |
| `KeyDirectionSort` | `?order_by=created_at&order=desc` | No (raises `UnsupportedSortError` on multi-term) |

Sort strategies return a `SortEncoding`, the sort-side analogue of
`FilterEncoding`. It carries both ordinary key/value `params` and an
optional `raw_query` fragment for dialects whose sort expression must be
emitted verbatim into the query string instead of as `key=value` pairs
(e.g. an RQL-style `ordering(+name,-created_at)` term). When both the
filter and sort strategies emit a `raw_query` fragment they are
concatenated into the URL with `&` separators alongside the URL-encoded
ordinary params.

## Wiring strategies to a client

Pass them as keyword-only arguments at construction time:

```python
from acme.commerce import CommerceClient
from acme.commerce.base.strategies import (
    LimitOffsetPagination, KeyOpValueFilter, CommaSignedSort,
)

client = CommerceClient(
    base_url="https://api.example.com",
    pagination_strategy=LimitOffsetPagination(
        default_page_size=50, total_field="meta.pagination.total",
    ),
    filter_strategy=KeyOpValueFilter(),
    sort_strategy=CommaSignedSort(),
)
```

Strategies are stateless (they translate, they don't store), so a
single instance per client is correct. They read attributes from the
client at call time, which is why `with_shape("dicts")` and similar
sibling-client tricks keep the strategies attached transparently.

You can also bake strategy choice into your user-layer `Client`
subclass so callers don't have to think about it:

```python
# my_client/client.py — emitted once, then yours
import httpx
from acme.commerce.base.client import CommerceClientBase
from acme.commerce.base.strategies import (
    LimitOffsetPagination, KeyOpValueFilter, CommaSignedSort,
)


class CommerceClient(CommerceClientBase):
    def __init__(self, *, api_key: str) -> None:
        super().__init__(
            base_url="https://api.example.com",
            auth=BearerAuth(api_key),
            pagination_strategy=LimitOffsetPagination(
                default_page_size=50, total_field="meta.pagination.total",
            ),
            filter_strategy=KeyOpValueFilter(),
            sort_strategy=CommaSignedSort(),
        )
```

## Writing your own strategy

When the API does something non-standard, write a small class with the
right shape. No inheritance, no registration — just match the Protocol.

### A custom pagination strategy

Suppose your API uses `?from=<id>&take=<n>` and reports the total in a
custom header `X-Items-Total`:

```python
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from acme.commerce.base.exceptions import UnsupportedPaginationError


@dataclass
class FromTakePagination:
    default_page_size: int
    take_param: str = "take"
    from_param: str = "from"
    id_field: str = "id"
    total_header: str = "X-Items-Total"

    @property
    def supports_count(self) -> bool:
        return True

    @property
    def supports_random_access(self) -> bool:
        # Page N's `from=` is the id of the last item on page N-1, so we
        # can't compute a page directly — only by walking from the start.
        return False

    def initial(self, page_size: int | None) -> Mapping[str, Any]:
        return {
            self.take_param: page_size if page_size is not None else self.default_page_size,
        }

    def next(
        self, response: httpx.Response, last_params: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        items = self.extract_items(response)
        if not items:
            return None
        return {**last_params, self.from_param: items[-1][self.id_field]}

    def extract_items(self, response: httpx.Response) -> list[Any]:
        body = response.json()
        return list(body) if isinstance(body, list) else []

    def count_request_params(self, base_params: Mapping[str, Any]) -> Mapping[str, Any]:
        return {**base_params, self.take_param: 1}

    def extract_count(self, response: httpx.Response) -> int:
        return int(response.headers[self.total_header])

    def page_params(
        self, page_num: int, page_size: int | None
    ) -> Mapping[str, Any]:
        # Random access is impossible here; the collection won't call this
        # because supports_random_access is False, but the Protocol requires
        # the method to exist.
        raise UnsupportedPaginationError(
            "FromTakePagination is sequential — iterate the collection instead"
        )
```

Plug it in just like a built-in:

```python
client = CommerceClient(
    base_url="...", pagination_strategy=FromTakePagination(default_page_size=100),
)
```

### A custom filter strategy

If your API expects RQL (`?and(eq(status,open),eq(customer,42))`):

```python
from dataclasses import dataclass

from acme.commerce.base.filters import (
    AndFilter, Filter, NotFilter, OrFilter, Search,
)
from acme.commerce.base.strategies import FilterEncoding


@dataclass
class RqlFilter:
    def encode(self, f: Filter | None) -> FilterEncoding:
        if f is None:
            return FilterEncoding()
        return FilterEncoding(raw_query=self._render(f))

    def _render(self, node: Filter) -> str:
        if isinstance(node, AndFilter):
            return f"and({self._render(node.left)},{self._render(node.right)})"
        if isinstance(node, OrFilter):
            return f"or({self._render(node.left)},{self._render(node.right)})"
        if isinstance(node, NotFilter):
            return f"not({self._render(node.operand)})"
        if isinstance(node, Search):
            return f"like(*,{node.query})"
        # leaf with kwargs
        return ",".join(f"eq({k},{v})" for k, v in node.kwargs.items())
```

Use `raw_query` (not `params`) so the operators stay verbatim in the
URL.

### A custom sort strategy

If your API encodes sort as `?sortField=<f>&sortDir=<asc|desc>` and
allows only one field:

```python
from dataclasses import dataclass

from acme.commerce.base.exceptions import UnsupportedSortError
from acme.commerce.base.sort import Sort
from acme.commerce.base.strategies import SortEncoding


@dataclass
class SingleFieldSort:
    field_param: str = "sortField"
    direction_param: str = "sortDir"

    def encode(self, s: Sort | None) -> SortEncoding:
        if not s:
            return SortEncoding()
        if len(s.terms) > 1:
            raise UnsupportedSortError("SingleFieldSort accepts one term only")
        field_, direction = s.terms[0]
        return SortEncoding(
            params={self.field_param: field_, self.direction_param: direction},
        )
```

If your API expects an RQL-style ordering expression
(`?ordering(+name,-created_at)`) where the parentheses and commas must
not be URL-encoded, emit a `raw_query` fragment instead of `params`:

```python
from dataclasses import dataclass

from acme.commerce.base.sort import Sort
from acme.commerce.base.strategies import SortEncoding


@dataclass
class RqlOrdering:
    def encode(self, s: Sort | None) -> SortEncoding:
        if not s:
            return SortEncoding()
        terms = [
            f"-{field_}" if direction == "desc" else f"+{field_}"
            for field_, direction in s.terms
        ]
        return SortEncoding(raw_query="ordering(" + ",".join(terms) + ")")
```

Use `raw_query` (not `params`) so the operators stay verbatim in the
URL. When the configured filter strategy also emits a `raw_query` (e.g.
`RqlFilter`), the two fragments are joined with `&` in the final URL —
for example `/orders?and(eq(status,open))&ordering(-created_at)&offset=0&limit=50`.

## Errors strategies are allowed to raise

When the user composes a query the strategy can't represent, raise the
matching runtime error. The generated client surfaces it to the caller
with a clear message:

* `UnsupportedFilterError` — the filter tree contains a leaf or operator
  the strategy can't encode (e.g. an `OR` against `KeyValueFilter`).
* `UnsupportedSortError` — the sort term list is incompatible (e.g.
  multi-term against `KeyDirectionSort`).
* `UnsupportedPaginationError` — the collection asked the strategy for
  something the wire protocol fundamentally doesn't allow (e.g.
  `count()` against a strategy with no count source, or `get_page(...)`
  against a sequential strategy like `CursorPagination`).
* `ConfigurationError` — the strategy is missing configuration needed
  for the operation (e.g. `extract_count` called on a pagination
  strategy without a count source).

These are all importable from `<your_pkg>.base.exceptions`.
