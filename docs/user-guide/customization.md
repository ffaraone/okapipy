# Code customization

okapipy is designed for the case that almost always happens in practice:
the OpenAPI spec changes, you re-run the generator, and you don't want
your hand-written code to be touched. The whole customization model is
shaped around that one goal.

The four properties we want:

1. **Lossless regeneration.** Re-running the generator on a changed
   spec never overwrites user-authored code.
2. **No DSL, no markers.** Customization is plain Python — subclassing
   and method definitions, not protected-region comments or template
   overrides.
3. **Single source of truth per concern.** A given method or class is
   owned either by the generator or by you; never co-edited.
4. **Type-safe by default.** `mypy --strict` on the user layer catches
   breakage when the generator's signatures shift after a spec change.

## The two layers

The generator emits **two cooperating layers** rooted at your package:

| Layer | Location | Owner | Lifecycle |
| --- | --- | --- | --- |
| **Base** | `my_client/base/` | Generator | Rewritten on every `okapipy generate` run. |
| **User** | `my_client/*.py` | You | Emitted once, on first generation; never overwritten. |

The base layer holds machine-translated code: HTTP wiring,
request/response models, transport plumbing, vendored runtime. The user
layer holds the public surface and any custom methods, overrides, or
hand-written operations.

The **only rule** you have to internalize: never edit anything under
`base/`. Every customization lives in the user layer.

## Naming convention

* **Base package:** `base/` (no underscore prefix; ordinary importable
  package).
* **Base classes:** PascalCase + `Base` suffix — `CommerceClientBase`,
  `OrdersCollectionBase`, `OrderResourceBase`,
  `OrderSubmitActionBase`.
* **User classes:** drop the `Base` suffix —
  `CommerceClient(CommerceClientBase)`,
  `OrdersCollection(OrdersCollectionBase)`,
  `OrderResource(OrderResourceBase)`.
* **Sub-trees by kind.** Generated base modules live in
  `base/collections/`, `base/resources/`, `base/singletons/`,
  `base/actions/`, `base/namespaces/`. The user-layer modules live one
  level up at the package root, one file per top-level node.
* **Models** (Pydantic request/response types) live in `base/models.py`
  and have no `Base` suffix — they're values, not behavior.

## File layout

For a spec containing namespace `commerce` with collection
`/commerce/orders`, resource action `/commerce/orders/{id}/submit`, and
sub-collection `/commerce/orders/{id}/lines`:

```
my_client/
├── __init__.py             # one-shot — re-exports CommerceClient
├── client.py               # one-shot — class CommerceClient(CommerceClientBase)
├── commerce.py             # one-shot — namespace shim
├── orders.py               # one-shot — class OrdersCollection, OrderResource, ...
└── base/
    ├── __init__.py         # regenerated
    ├── client.py           # regenerated — class CommerceClientBase
    ├── strategies.py       # regenerated — vendored from okapipy.generator.runtime
    ├── filters.py          # regenerated — vendored
    ├── sort.py             # regenerated — vendored
    ├── transport.py        # regenerated — vendored (RetryPolicy, RetryTransport, …)
    ├── exceptions.py       # regenerated — vendored ApiError tree
    ├── types.py            # regenerated — vendored (UNSET, RequestOptions)
    ├── models.py           # regenerated — Pydantic request/response models
    ├── collections/orders.py
    ├── resources/order.py
    ├── actions/order_submit.py
    ├── namespaces/commerce.py
    └── _generated.json
```

A larger spec produces one user-layer module per namespace and one per
top-level collection. Collections under the root (no namespace) live in
`my_client/<collection>.py`.

## How the wiring works

For every parser-tree node, the generator emits a `Base` class with
these properties:

* **All transport, serialization, and validation logic** lives in
  `Base` methods.
* **All structural wiring** between parents and children
  (`Client.orders`, `OrdersCollection["ord_42"]` returning an
  `OrderResource`) is emitted on the `Base` class. The user layer never
  owns wiring — only specialization.
* **Every method is overridable.** No `final`, no name mangling, no
  `__slots__` blocking subclass attributes.

### Factory hooks

Every parent class exposes a `__<child>_factory__` ClassVar defaulting
to the corresponding `*Base` type. Properties and `__getitem__`
accessors instantiate children through that hook, so the user layer can
swap in its own subclass without touching the wiring:

```python
# my_client/base/client.py — REGENERATED
class CommerceClientBase:
    __orders_factory__: ClassVar[type[OrdersCollectionBase]] = OrdersCollectionBase

    @property
    def orders(self) -> OrdersCollectionBase:
        return self.__orders_factory__(client=self, path_params={})
```

```python
# my_client/base/collections/orders.py — REGENERATED
class OrdersCollectionBase:
    __resource_factory__: ClassVar[type[OrderResourceBase]] = OrderResourceBase

    def __getitem__(self, id: Any) -> OrderResourceBase:
        return self.__resource_factory__(
            client=self.client, path_params={**self.path_params, "id": id},
        )
```

### Auto-wired stubs

On first generation, the generator emits one stub per public surface
and each non-leaf stub pre-binds every `__<child>_factory__` hook on
the corresponding base class to point at the user-layer subclass:

```python
# my_client/orders.py — emitted once, then yours
from my_client.base.collections.orders import (
    OrdersCollectionBase, AsyncOrdersCollectionBase,
)
from my_client.base.resources.order import (
    OrderResourceBase, AsyncOrderResourceBase,
)


class OrderResource(OrderResourceBase):
    pass


class AsyncOrderResource(AsyncOrderResourceBase):
    pass


class OrdersCollection(OrdersCollectionBase):
    __resource_factory__ = OrderResource


class AsyncOrdersCollection(AsyncOrdersCollectionBase):
    __resource_factory__ = AsyncOrderResource
```

The result: `client.commerce.orders["ord_42"]` returns an
`OrderResource` (your subclass) immediately, with no manual wiring
needed.

Every regenerated file carries a header so you don't accidentally edit
one:

```python
"""Generated by okapipy — edit the user-layer subclass, not this base.
...
"""
```

## Customization patterns

### Augment a generated method

The generated method is mostly correct; you want to wrap it (logging,
metrics, retries, validation):

```python
# my_client/orders.py
class OrderResource(OrderResourceBase):
    def retrieve(self, **overrides: Any) -> Any:
        metrics.incr("order.retrieve", id=self.path_params["id"])
        return super().retrieve(**overrides)
```

The override delegates to `super().retrieve(...)`, so behavior tracks
any future regeneration of the base method.

### Replace a generated method entirely

The generator's output is wrong for your case — different signature,
different semantics, or backed by a different endpoint. Two-step:

1. **Suppress emission** in the rules file:

    ```yaml
    # rules.yaml
    paths:
      /commerce/orders/{id}:
        x-okapipy-exclude: [PATCH]
    ```

   The generator now does *not* emit `partial_update()` on
   `OrderResourceBase`.

2. **Define your own** in the user layer:

    ```python
    class OrderResource(OrderResourceBase):
        def partial_update(self, *, idempotency_key: str, **fields: Any) -> Any:
            return self.client._http.request(
                "PATCH", self.url, json=fields,
                headers={"Idempotency-Key": idempotency_key},
            ).json()
    ```

The op is fully user-owned. Regeneration leaves it alone because the
base never had it.

### Add an operation that isn't in the spec

Define the method on your subclass. No spec change, no rules entry
needed:

```python
class OrdersCollection(OrdersCollectionBase):
    def for_customer(self, customer_id: str) -> "OrdersCollection":
        # Reuse the base's filter accumulator.
        from my_client.base.filters import Filter
        return self.filter(Filter(customer_id=customer_id))
```

```python
client.commerce.orders.for_customer("cust_42").order_by("-created_at")
```

### Customize iteration

The collection's `__iter__` is on `OrdersCollectionBase` and returns an
iterator that drives the configured pagination strategy. To intercept
each item — for caching, instrumentation, or shape transformation —
override `__iter__`:

```python
class OrdersCollection(OrdersCollectionBase):
    def __iter__(self):
        for order in super().__iter__():
            cache[order.id] = order
            yield order
```

Or to swap the iterator entirely (e.g. to use a different pagination
strategy just for this collection), wire a custom iterator class in the
factory hook — the generator emits `<Collection>Iterator` next to each
collection class.

!!! note
    The `<Collection>Iterator` class only exists for collections whose
    fetch operation is paginated. When the fetch carries
    [`x-okapipy-paginated: false`](rules.md#x-okapipy-paginated-opt-out-of-pagination),
    iteration is an inline generator on the collection itself — there
    is no iterator class to swap. Overriding `__iter__` to wrap each
    item still works exactly as shown above.

### Override the constructor / authentication

The user-layer `Client` is the right place to wire authentication,
retries, base URLs, and any cross-cutting concerns:

```python
# my_client/client.py
import httpx
from my_client.base.client import CommerceClientBase
from my_client.base.transport import RetryPolicy


class BearerAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self.token = token
    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class CommerceClient(CommerceClientBase):
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        super().__init__(
            base_url=base_url or "https://api.example.com",
            auth=BearerAuth(api_key),
            retries=RetryPolicy(total=3, backoff=0.5),
            headers={"User-Agent": "acme-commerce/1.0"},
        )
```

Callers stop having to pass URLs and auth on every instantiation:

```python
with CommerceClient(api_key="...") as client:
    ...
```

### Narrow a return type for IDE autocomplete

By default, `client.orders` is statically typed as
`OrdersCollectionBase`. That's correct at runtime (it returns whatever
`__orders_factory__` points at), but IDEs can't see your custom methods.
Annotate the inherited property at the class level using `TYPE_CHECKING`
to avoid runtime cost:

```python
from typing import TYPE_CHECKING

class CommerceClient(CommerceClientBase):
    __orders_factory__ = OrdersCollection
    if TYPE_CHECKING:
        orders: OrdersCollection   # narrows the inherited property's return type
```

Purely cosmetic; runtime behavior is unchanged.

### A new collection appears

Walk-through. The customer adds `/commerce/products` to their OpenAPI
spec and reruns `okapipy generate`.

**Before:**

```
my_client/
├── client.py            class CommerceClient(...): __commerce_factory__ = CommerceNamespace
├── commerce.py          class CommerceNamespace(...): __orders_factory__ = OrdersCollection
├── orders.py
└── base/
    ├── client.py
    ├── namespaces/commerce.py
    └── collections/orders.py
```

**After regeneration:**

```
my_client/
├── client.py            (UNTOUCHED — already wires __commerce_factory__)
├── commerce.py          (UNTOUCHED — does NOT yet wire __products_factory__)
├── orders.py            (UNTOUCHED)
├── products.py          (NEW — class ProductsCollection(ProductsCollectionBase): pass)
└── base/
    ├── client.py        (REWRITTEN)
    ├── namespaces/commerce.py    (REWRITTEN — gains __products_factory__ + @property products)
    ├── collections/orders.py     (unchanged content if no spec drift)
    └── collections/products.py   (NEW)
```

The generator emits a drift-detection warning telling you what to wire:

```
WARNING: 1 new child not yet wired into a user-layer parent.
  src/my_client/commerce.py
    + __products_factory__ = ProductsCollection         (on CommerceNamespace)
    + __products_factory__ = AsyncProductsCollection    (on AsyncCommerceNamespace)
  Until you add this, `client.commerce.products` returns ProductsCollectionBase
  rather than your ProductsCollection subclass.
```

Two choices:

* **Ignore the warning.** `client.commerce.products` works immediately
  using the base default factory — fully functional, just untyped if
  you're using `TYPE_CHECKING` narrowings.
* **Apply the warning's suggestion.** Add the two lines. Now
  `client.commerce.products` returns your `ProductsCollection`, ready
  to carry overrides.

The drift detector is part of `okapipy generate --check`. It
exits non-zero when unwired children are detected, so a CI run after a
spec bump fails until someone makes the call.

## Regeneration semantics

Each `okapipy generate` run does:

1. **Parse** the spec + rules file into the parser tree.
2. **Plan** the file set — every `base/**` file, plus stubs for any
   new namespace/collection/resource without an existing user-layer
   file.
3. **Write** every `base/**` file unconditionally. Existing content is
   overwritten.
4. **Write** user-layer stubs **only when the target path does not yet
   exist**. Existing user files are left strictly alone — no diff, no
   merge, no warning.
5. **Prune** stale `base/**` files that correspond to removed
   namespaces/collections. User-layer files are **never pruned**, even
   if the matching base file is gone — you may still want them.
6. **Emit a state file** at `base/_generated.json` recording the
   generator version, generation timestamp, the list of base files
   written, and every parent → child wiring edge. Used to detect drift
   and to power `--check`.

### Failure modes regeneration intentionally surfaces

* **Renamed operation.** Spec renames `submit` → `confirm`. Base class
  loses `submit`, gains `confirm`. Your override calling
  `super().submit()` no longer overrides anything; mypy catches the
  reference, tests catch the behavior. Rename and move on.
* **Changed signature.** Spec adds a required parameter to a method.
  Your `super().method(...)` call breaks at runtime and at type-check
  time. Right failure mode — silent drift would be worse.
* **Removed operation.** Base loses the method. Your override (if it
  called `super()`) breaks. If you want to keep the method, drop the
  `super()` and reimplement; if you wanted it gone too, delete the
  override.

## Tooling

* **`okapipy generate --check`** — exits non-zero if any base file
  would change, any drift warning fires, or any stale file would be
  pruned. CI gate to ensure regeneration is committed.
* **`okapipy generate --quiet`** — suppress drift-detection
  warnings. Pruning still runs.
* **`shape: models | dicts`** (in `okapipy.yml`) — lock the generated
  client to a single [response shape](shapes.md). Omit for the
  dual-shape default (constructor `shape=` + `with_shape()`);
  `shape: dicts` also skips `base/models.py`.
* **`templates_dir:` and `model_templates_dir:`** (in `okapipy.yml`)
  — see [Templates](templates.md) for per-project overrides of the
  Jinja templates that drive code emission.

## Out of scope

* Protected-region markers in generated files.
* Hooks / plugin entry points that mutate the parser tree before
  generation.
* Cross-language output. okapipy generates Python; other targets are a
  separate project.
