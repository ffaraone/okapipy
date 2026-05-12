# Using the client

This page is the field guide to every shape the generated client
exposes. Code samples assume a client generated with
`--package acme.commerce --client-class CommerceClient` from a spec that
contains a `commerce` namespace, an `orders` collection with a resource,
sub-collection `lines`, an `submit` action, and an `auth` namespace.
Adapt the names to your own spec.

## Constructing a client

The sync client is a context manager:

```python
from acme.commerce import CommerceClient

with CommerceClient(base_url="https://api.example.com") as client:
    ...
```

The async sibling uses `async with`:

```python
from acme.commerce import AsyncCommerceClient

async with AsyncCommerceClient(base_url="https://api.example.com") as client:
    ...
```

Without the context manager you must call `.close()` (or `await
.aclose()`) yourself when you're done — the underlying `httpx` client
keeps connections in a pool that should be cleaned up at process exit.

### Constructor options

Both `CommerceClient` and `AsyncCommerceClient` accept the same
keyword-only options:

```python
import httpx
from acme.commerce import CommerceClient
from acme.commerce.base.transport import RetryPolicy
from acme.commerce.base.strategies import (
    LimitOffsetPagination,
    KeyOpValueFilter,
    CommaSignedSort,
)

client = CommerceClient(
    base_url="https://api.example.com",
    auth=httpx.BasicAuth("user", "pass"),         # or any httpx.Auth
    timeout=httpx.Timeout(10.0, connect=5.0),     # forwarded to httpx
    retries=RetryPolicy(total=3, backoff=0.5),    # GET-only, see below
    transport=None,                               # custom httpx transport
    headers={"User-Agent": "acme-commerce/1.0"},  # default headers
    shape="models",                               # "models" or "dicts"
    pagination_strategy=LimitOffsetPagination(default_page_size=100),
    filter_strategy=KeyOpValueFilter(),
    sort_strategy=CommaSignedSort(),
)
```

| Option | Type | Notes |
| --- | --- | --- |
| `base_url` | `str` | Required. Forwarded to `httpx.Client(base_url=...)`. |
| `auth` | `httpx.Auth \| None` | Any `httpx.Auth`: `BasicAuth`, `DigestAuth`, your own. |
| `timeout` | `httpx.Timeout \| float \| None` | Pass `None` to disable; omit to use httpx's default. |
| `retries` | `RetryPolicy \| None` | Wraps the transport in `RetryTransport` (GET only). |
| `transport` | `httpx.BaseTransport \| None` | Custom transport (mock, fixture, instrumentation). |
| `headers` | `Mapping[str, str] \| None` | Default headers merged into every request. |
| `shape` | `"models" \| "dicts"` | Only present when the client was generated without `--shape` (dual-shape mode). See [Response shape](shapes.md). |
| `pagination_strategy` | strategy or `None` | Defaults to `LimitOffsetPagination(default_page_size=100)`. |
| `filter_strategy` | strategy or `None` | Defaults to `KeyValueFilter()`. |
| `sort_strategy` | strategy or `None` | Defaults to `CommaSignedSort()`. |

## Collections

A collection is the URL of a list endpoint (`/commerce/orders`). The
generated `OrdersCollection` exposes a small fluent API for filtering,
sorting, pagination, and per-call overrides — and a few terminal calls
that actually issue requests.

### Iterate the full result set

```python
for order in client.commerce.orders:
    print(order.id, order.total)
```

That's a real Python iterator. The first `next()` issues a request,
yields rows from the page, then issues the next page when the current
one is exhausted, and stops when the configured pagination strategy
reports there are no more pages.

Async:

```python
async for order in client.commerce.orders:
    print(order.id, order.total)
```

### Filter, sort, paginate (fluent)

```python
from acme.commerce.base.filters import Filter
from acme.commerce.base.sort import Sort

results = (
    client.commerce.orders
    .filter(Filter(status="open"))
    .filter(Filter(customer_id="cust_42"))    # AND'd with the previous
    .order_by("-created_at")                  # string shorthand for desc
    .order_by(Sort("id"))                     # multi-term sort
    .page_size(50)
)

for order in results:
    ...
```

* `filter(expr)` accumulates with boolean **AND**. Call it multiple
  times to build up a conjunction.
* `order_by(term)` accepts a `Sort` object or a string (`"created_at"`
  ascending, `"-created_at"` descending, `"+created_at"` explicit
  ascending).
* `page_size(n)` overrides the strategy's default for this query.

### Get just the first item

```python
first_open = (
    client.commerce.orders
    .filter(Filter(status="open"))
    .order_by("-created_at")
    .first()
)
if first_open is None:
    print("no open orders")
```

`first()` returns the first item (or `None`) without consuming the rest
of the iterator.

### Count the result set

```python
total = client.commerce.orders.filter(Filter(status="open")).count()
```

`count()` issues one minimal request and reads the strategy-specific
total (e.g. the `total` field in the response envelope, the
`X-Total-Count` header, or the `Content-Range` header). It raises
`UnsupportedPaginationError` if the configured pagination strategy
doesn't support count.

!!! warning "Breaking change in this release"
    `count()` previously raised `NotImplementedError` when the strategy
    had no count source. It now raises `UnsupportedPaginationError`
    (importable from `<your_pkg>.base.exceptions`). If you were catching
    `NotImplementedError` here, switch to the new exception.

### Check whether the collection has any rows

```python
if client.commerce.orders.filter(Filter(status="open")).exists():
    notify_oncall()
```

`exists()` is exactly `count() > 0` — it issues the same minimal
request and short-circuits on the total. It raises the same
`UnsupportedPaginationError` when the strategy has no count source.

### Fetch a single page directly

```python
second_page = (
    client.commerce.orders
    .filter(Filter(status="open"))
    .page_size(50)
    .get_page(1)         # 0-indexed: 0 is the first page
)
```

`get_page(page_num)` returns the items on the N-th page (zero-indexed)
without walking the pages before it. Page size honors `.page_size(...)`
or the strategy's default; filters, sort, and `with_options(...)` apply
exactly as on iteration.

This is the right primitive for **parallel page fetches** — see the
[Cookbook](#parallel-page-fetches) for the full pattern.

`get_page` raises `UnsupportedPaginationError` for pagination strategies
that walk pages sequentially (`CursorPagination`, `LinkHeaderPagination`):
they cannot reach page N without first consuming page N-1's continuation
token.

### Create a record

```python
from acme.commerce.base.models import OrderCreate

new = client.commerce.orders.create(
    body=OrderCreate(customer_id="cust_42", lines=[...]),
)
```

`create(body, **overrides)` POSTs to the collection URL. `body` accepts
either a Pydantic model instance (it'll be `model_dump`-ed) or a plain
dict. `**overrides` accepts `params=` and `headers=` for one-off
per-request tweaks.

### Per-collection overrides

```python
orders = (
    client.commerce.orders
    .with_options(
        params={"include": "lines"},
        headers={"X-Customer-Id": "cust_42"},
        timeout=30.0,
    )
)

for order in orders:                          # every request carries the overrides
    ...

count = orders.count()                        # …including count() and create()
```

`with_options(**overrides)` seeds cross-cutting overrides for **every**
request the collection issues — listing, count, create, the lot. It's
the right place for tenant headers, `include=` query params, longer
timeouts, custom auth, and the like. Accepts: `params`, `headers`,
`timeout`, `auth`, `verify`, `retries`. Each call merges with prior
calls (params and headers are shallow-merged; everything else replaces).

### Identity

```python
orders = client.commerce.orders.all()        # returns self; reads nicer at call sites
```

`.all()` is a fluent identity. It exists so query chains read as
"every order matching X" rather than just "orders":

```python
client.commerce.orders.all().filter(...).order_by(...)
```

### Aggregate views on a collection

A collection can host singleton sub-views — endpoints that aggregate
over the items rather than being one of them. `/orders/stats`,
`/datasets/summary`, and `/workspaces/current/secrets/encrypted` all fit
this shape. The generator wires them as `@property` on the collection
class, so the call site reads naturally:

```python
stats = client.commerce.orders.stats.retrieve()    # → OrderStatsSingleton
```

Iteration and filtering on the collection itself still work as before;
the sub-singleton just sits alongside them. See
[Rules and extensions → Aggregate view on a collection](rules.md#an-aggregate-view-on-a-collection-ordersstats)
for the rules-file hint that triggers this shape.

## Resources

A resource is one item by id. You get to it by indexing the parent
collection:

```python
order_handle = client.commerce.orders["ord_42"]
```

That call **does not** issue a request — it returns a handle bound to
the right URL. CRUD verbs each issue one:

```python
order = order_handle.retrieve()                          # GET
updated = order_handle.update(body=OrderUpdate(...))     # PUT
patched = order_handle.partial_update(body={"status": "open"})  # PATCH
order_handle.delete()                                    # DELETE
```

Each accepts `**overrides` (`params=`, `headers=`):

```python
order = client.commerce.orders["ord_42"].retrieve(
    params={"include": "lines,events"},
    headers={"X-Trace-Id": "abc123"},
)
```

### Sub-collections, sub-resources, sub-actions

Anything that hangs off the resource path is reachable as a property:

```python
# sub-collection
for line in client.commerce.orders["ord_42"].lines:
    print(line.sku, line.qty)

# sub-resource (collection inside the resource → indexed again)
line = client.commerce.orders["ord_42"].lines["line_7"].retrieve()

# sub-action
client.commerce.orders["ord_42"].submit.run()
```

The chain reads top-down through the API tree.

### Singletons

A singleton is a resource with no enclosing collection (`/me`,
`/health`, `/users/{id}/avatar`). It exposes the same CRUD verbs as a
resource:

```python
me = client.me.retrieve()
client.me.update(body={"display_name": "..."})
client.users["usr_1"].avatar.delete()
```

Sub-collections and sub-actions on singletons work the same way:

```python
for note in client.me.notifications:
    ...

client.me.refresh.run()
```

## Actions

Actions are non-CRUD verb endpoints (`/login`, `/orders/{id}/submit`,
`/auth/refresh`). They're reachable as properties; the property returns
a small object whose only public method is `run(...)`:

```python
# Action that takes a request body.
token = client.auth.tokens.run(
    body={"username": "...", "password": "..."},
)

# Action without a body.
client.commerce.orders["ord_42"].submit.run()

# Action with overrides.
client.commerce.orders["ord_42"].submit.run(
    headers={"Idempotency-Key": "k_001"},
)
```

Why a `run()` method instead of calling the action directly? Because
the action object can hold its own configuration before `run()` —
identical to how collections accumulate filter / sort / page-size state.
Today the only options are `params=` and `headers=` overrides, but the
shape leaves room.

The return type follows the operation's response schema: a Pydantic
model, a list, a `dict[str, Any]` (in dicts shape, or when no schema
name was recovered), or `None` for `204 No Content`. See
[Response shape](shapes.md) for the full picture.

## Namespaces

Namespaces are routers — they own no operations of their own, just
children. They're reachable as properties:

```python
auth = client.auth                   # AuthNamespace
commerce = client.commerce           # CommerceNamespace

token = client.auth.tokens.run(body={...})
for order in client.commerce.orders:
    ...
```

You typically don't store namespace handles in variables; just call
through them.

## Filters

Filter expressions are built with the `Filter` class from
`<package>.base.filters`. The default constructor takes kwargs
(Django-Q style); composition operators build the tree:

```python
from acme.commerce.base.filters import Filter, Search

# Equality.
Filter(status="open")

# Operator suffixes (when the strategy supports them — KeyOpValueFilter).
Filter(created_at__gte="2026-01-01", status__in=["open", "pending"])

# Boolean composition.
(Filter(status="open") | Filter(status="pending")) & ~Filter(deleted=True)

# Free-text search (with a strategy that supports it).
Search("running shoes")
```

Pass the resulting tree to `.filter(...)`:

```python
from acme.commerce.base.filters import Filter

(
    client.commerce.orders
    .filter(Filter(status__in=["open", "pending"]) & Filter(customer_id="cust_42"))
)
```

The configured `FilterStrategy` decides how the tree is encoded on the
wire. Some strategies don't support OR or NOT and will raise
`UnsupportedFilterError` when you try; see [Strategies](strategies.md).

## Sorting

`Sort` from `<package>.base.sort` holds a list of `(field, direction)`
terms:

```python
from acme.commerce.base.sort import Sort

Sort("created_at")                   # ascending
Sort("-created_at")                  # descending (leading `-` shorthand)
-Sort("created_at")                  # unary minus also flips direction
Sort("status") + Sort("-created_at") # multi-term: concatenate
```

The collection's `.order_by(...)` accepts either a `Sort` instance or a
string:

```python
client.commerce.orders.order_by("status").order_by("-created_at")
client.commerce.orders.order_by(Sort("status") + Sort("-created_at"))
# Both produce the same wire output via CommaSignedSort.
```

## Response shape

The shape of every method's body parameter and return type — typed
Pydantic models, raw dicts, or both — is settled at generation time by
the `--shape` flag of `okapipy spec generate`. The default emits a
dual-shape client where the constructor takes a `shape="models"|"dicts"`
keyword and `with_shape(...)` returns a sibling that flips it at
runtime; `--shape models` and `--shape dicts` produce locked clients
without the runtime switch.

[Response shape](shapes.md) is the full story: which signatures each
mode produces, when to pick which, and the dual-flavor recipe (one
project, two clients — typed and raw).

## Authentication

Pass any `httpx.Auth` — built-in or custom — through the `auth=`
constructor option:

```python
import httpx

# Bearer token via a custom one-liner.
class BearerAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self.token = token
    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request

client = CommerceClient(
    base_url="https://api.example.com",
    auth=BearerAuth(token="..."),
)

# Built-ins: BasicAuth, DigestAuth, NetRCAuth.
client = CommerceClient(base_url=..., auth=httpx.BasicAuth("user", "pass"))
```

For OAuth2 client-credentials flows, fetch the token through the
generated client itself, then build a second client with it:

```python
with CommerceClient(base_url="https://api.example.com") as bootstrap:
    token = bootstrap.auth.tokens.run(
        body={"client_id": "...", "client_secret": "..."},
    )

with CommerceClient(
    base_url="https://api.example.com",
    auth=BearerAuth(token=token.access_token),
) as client:
    ...
```

## Retries

Retries apply to **GET requests only** (per design — POST/PUT/PATCH/DELETE
are not assumed to be idempotent). Configure them with `RetryPolicy`:

```python
from acme.commerce.base.transport import RetryPolicy

client = CommerceClient(
    base_url="https://api.example.com",
    retries=RetryPolicy(
        total=3,                                       # extra attempts
        backoff=0.5,                                   # delay = backoff * 2**attempt
        retry_on_status=frozenset({502, 503, 504}),    # default
        retry_on_exceptions=(httpx.TransportError,),   # default
    ),
)
```

`total=0` (the default) disables retries entirely. You can also pass
`retries=` to `with_options(...)` to configure retries just for one
collection.

## Errors

The generated client calls `response.raise_for_status()` on every
non-2xx response, so HTTP errors surface as `httpx.HTTPStatusError`:

```python
import httpx

try:
    order = client.commerce.orders["ord_does_not_exist"].retrieve()
except httpx.HTTPStatusError as exc:
    print(exc.response.status_code)        # e.g. 404
    print(exc.response.json())             # the error body
```

Strategy errors raise from the runtime exceptions module:

```python
from acme.commerce.base.exceptions import (
    UnsupportedFilterError,
    UnsupportedSortError,
    ConfigurationError,
)

try:
    client.commerce.orders.filter(Filter(role="admin") | Filter(role="owner"))
    for order in client.commerce.orders:
        ...
except UnsupportedFilterError as exc:
    # The configured KeyValueFilter doesn't accept OR.
    print(exc)
```

The `<package>.base.exceptions` module also defines `ApiError`,
`ClientError` (4xx), `ServerError` (5xx), and
`ResponseValidationError` — these are the shape future versions will
move toward as the runtime evolves.

## IDE tooltips

The generated client is built to be navigated by hover. Every class
your IDE shows when you type `client.` carries a markdown docstring
that opens with a one-line summary and lists the children you can
reach from there — sub-namespaces, collections, singletons, actions —
each with the property name, the class it returns, and a short
description.

For example, hovering the client class itself surfaces something like:

```text
HTTP client for `acme-commerce` (v1.4.2).

Construct with `base_url=...`. Configure pagination, filter, and sort
strategies via the matching keyword arguments.

#### Top-level collections

- **`orders`** → `OrdersCollectionBase` — List orders.

#### Top-level namespaces

- **`auth`** → `AuthNamespaceBase` — Login and token endpoints.
```

Hovering a collection class shows the same kind of map plus the
runtime's standard query helpers (`.first()`, `.count()`, `.exists()`,
`.get_page(n)`) and any `.create(body)` the spec declared. Hovering a
resource class lists the CRUD verbs the spec actually populated, plus
sub-collections, sub-singletons, and actions reachable from the
resource.

The text comes from your OpenAPI document:

* **Operations** — the `summary` and `description` of each operation
  (`get`, `post`, …) flow into the matching method's docstring and
  into the bullet for that method's class.
* **Namespaces** — namespaces are synthesized from path segments and
  carry no spec-level prose of their own. Add a root `tags[]` entry
  whose `name` matches the namespace and your `description` shows up
  as the lead paragraph of the namespace class. A tag that matches no
  namespace is silently ignored, so tag descriptions are safe to add.

Property accessors (every `@property` and the collection's
`__getitem__`) carry a separate, shorter docstring intended for the
call-site hover. Those one-liners are deliberately sync/async-agnostic
— they never name a class explicitly, because the same accessor body
is reused for `Client` and `AsyncClient` and pinning either side would
mislead the other.

Class-docstring bullet targets always name the **sync** sibling
(`OrdersCollectionBase`, not `AsyncOrdersCollectionBase`); the actual
return type comes from the property's own annotation, which already
carries the right `Async`-prefixed name. Pylance, PyCharm, and Sphinx
all auto-link the bare name.

## Cookbook

Short recipes for common needs.

### Page-by-page processing (instead of row-by-row)

`for order in collection:` flattens pages into rows. If you want the raw
pages — for chunked persistence, batch processing, ETag handling — drive
the pages directly with `get_page`:

```python
import math

collection = (
    client.commerce.orders
    .filter(Filter(status="open"))
    .page_size(500)
)

page_size = 500
total = collection.count()

for page_num in range(math.ceil(total / page_size)):
    page = collection.get_page(page_num)
    if not page:                                # extra safety if total grew
        break
    persist_batch(page)
```

### Parallel page fetches

When pages are independent — bulk export, full-table sync, fan-out
processing — `get_page` runs them concurrently. The collection is safe
to share across threads or asyncio tasks for `get_page` calls because
the method reads query state but never writes to it.

!!! warning "Don't mutate the collection mid-flight"
    Don't call `.filter(...)` / `.page_size(...)` / `.with_options(...)`
    on the collection while parallel `get_page` calls are in flight —
    those *do* mutate state and can poison in-flight requests. Configure
    the collection up front, then fan out.

=== "Sync (`ThreadPoolExecutor`)"
    ```python
    import math
    from concurrent.futures import ThreadPoolExecutor

    page_size = 500
    collection = (
        client.commerce.orders
        .filter(Filter(status="open"))
        .page_size(page_size)
    )

    total = collection.count()
    num_pages = math.ceil(total / page_size)

    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(collection.get_page, range(num_pages)))

    orders = [order for page in pages for order in page]
    ```

=== "Async (`asyncio.gather`)"
    ```python
    import asyncio
    import math

    page_size = 500
    collection = (
        async_client.commerce.orders
        .filter(Filter(status="open"))
        .page_size(page_size)
    )

    total = await collection.count()
    num_pages = math.ceil(total / page_size)

    pages = await asyncio.gather(
        *(collection.get_page(i) for i in range(num_pages))
    )

    orders = [order for page in pages for order in page]
    ```

`get_page` requires a pagination strategy that supports random access —
`LimitOffsetPagination` and `PageNumberPagination`. Cursor and
link-header strategies are sequential and reject the call with
`UnsupportedPaginationError`. See [Pagination
strategies](strategies.md#built-in-pagination-strategies) for the
capability matrix.

### Tenant-scoped sub-clients

If your API is multi-tenant and you talk to several at once, build one
client per tenant — they share connection pools cheaply:

```python
shared = CommerceClient(base_url="https://api.example.com")

for tenant_id in tenant_ids:
    scoped = shared.with_shape(shared.shape)            # new sibling client
    # …or build a wrapper that injects the tenant header on every collection:
    orders = shared.commerce.orders.with_options(
        headers={"X-Tenant-Id": tenant_id},
    )
    for order in orders:
        process(tenant_id, order)
```

### Concurrent fetches (async)

```python
import asyncio

async with AsyncCommerceClient(base_url="...") as client:
    coros = [
        client.commerce.orders[oid].retrieve()
        for oid in order_ids
    ]
    orders = await asyncio.gather(*coros, return_exceptions=True)
```

### Mock the transport in tests

`httpx` ships a `MockTransport`; pass it through `transport=`:

```python
import httpx

def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/commerce/orders":
        return httpx.Response(200, json={"items": [...], "total": 1})
    return httpx.Response(404)

with CommerceClient(
    base_url="https://api.example.com",
    transport=httpx.MockTransport(handler),
) as client:
    orders = list(client.commerce.orders)
```

Combine `transport=httpx.MockTransport(...)` with `shape="dicts"` to
test against opaque payloads without spec-driven model classes.

### Switch pagination on a single collection

The pagination strategy is stored on the **client**, not per-collection.
If one endpoint paginates differently from the rest of the API, the
clean fix is to define your own user-layer subclass with a custom
`__iter__`. See [Code customization](customization.md#augment-a-generated-method)
for the full pattern.
