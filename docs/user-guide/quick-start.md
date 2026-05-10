# Quick start

This page walks you from "I have an OpenAPI document" to "I have a typed
Python client I can `import` and call." Five minutes, four commands.

For a tour of every API surface the generated client exposes — filters,
sorts, pagination, actions, async, error handling, retries —
see [Using the client](client-usage.md).

## 1. Pick (or grab) a spec

okapipy reads OpenAPI 3.x in JSON or YAML, from a local file **or** an
`http(s)` URL. For the rest of this page we'll use a local
`openapi.yaml`, but anywhere you see that, a URL works too.

## 2. Sanity-check the parse

Before generating code, see what okapipy *thinks* your spec looks like:

```bash
okapipy spec parse openapi.yaml
```

You'll get:

* a counts panel on stderr (namespaces, collections, resources,
  singletons, actions),
* a JSON dump of the parsed tree on stdout — pipe it into `less` or
  `jq`.

If you'd rather save the tree:

```bash
okapipy spec parse openapi.yaml --output tree.yaml
# .yaml or .yml → YAML; .json → JSON. Anything else errors.
```

If a classification looks off (`Staff` should be a collection, `me`
should be a singleton, a non-CRUD POST got dropped), fix it in the rules
file — see [Rules and extensions](rules.md) — before generating.

## 3. Generate a client

```bash
okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient
```

That writes a complete Python project under `./my-client`:

```
my-client/
├── pyproject.toml          # one-shot
├── README.md               # one-shot
├── LICENSE                 # one-shot
├── src/acme/commerce/
│   ├── __init__.py         # one-shot — your public surface
│   ├── client.py           # one-shot — class CommerceClient(CommerceClientBase)
│   ├── orders.py           # one-shot — class Order(OrderBase), OrdersCollection(...)
│   └── base/               # regenerated every run — DO NOT EDIT
│       ├── client.py
│       ├── collections/orders.py
│       ├── resources/order.py
│       ├── actions/order_submit.py
│       ├── models.py
│       ├── strategies.py
│       ├── filters.py
│       ├── sort.py
│       ├── transport.py
│       ├── exceptions.py
│       ├── types.py
│       └── _manifest.json
└── tests/                  # one-shot scaffolding
```

Two layers, two lifecycles: the `base/` tree is rewritten on every
`okapipy spec generate`, the rest is emitted **once** and then yours
forever. See [Code customization](customization.md) for the full story.

## 4. Use it

```python
from acme.commerce import CommerceClient

with CommerceClient(base_url="https://api.example.com") as client:
    # Iterate a collection — pagination is automatic.
    for order in client.commerce.orders:
        print(order.id, order.total)

    # Look up one resource by id (square brackets, not parens).
    order = client.commerce.orders["ord_42"].retrieve()

    # Sub-collections walk the tree.
    line = client.commerce.orders["ord_42"].lines.create(
        body={"sku": "SKU-1", "qty": 2},
    )

    # Actions are real properties returning an Action object whose
    # one method is `run(...)`.
    client.commerce.orders["ord_42"].submit.run()
```

That's the whole rhythm. Three observations to internalize:

1. **Listing is iteration**, not a `fetch()` method. `for order in
   client.commerce.orders:` issues paged GETs through the configured
   pagination strategy and yields parsed model instances.
2. **Resources use `[id]`**, not `(id)`. The collection implements
   `__getitem__`, returning the resource object — no network call yet.
3. **Actions have a single `.run()` method**. POST `/.../submit` is
   exposed as `submit.run()`, not `submit()`, because the action object
   itself can carry per-call state (overrides, headers, options).

The generated client is also built to be navigated by hover: every
class lists the children you can reach from it (sub-namespaces,
collections, actions, ...) right in its docstring, so typing `client.`
and reading the IDE tooltip is enough to discover the surface. See
[IDE tooltips](client-usage.md#ide-tooltips) for the full story.

## 5. Iterate

When the upstream spec changes, re-run the same command:

```bash
okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient
```

The `base/` tree is rewritten to match the new spec. Your subclasses,
your `client.py`, your `__init__.py` — all left strictly alone. If the
spec gained a new namespace or collection, the generator emits new
*one-shot* stubs for it and prints a drift-detection warning telling
you which factory line to add to wire your subclass into the tree.

In CI, gate on regeneration being committed:

```bash
okapipy spec generate openapi.yaml --output ./my-client \
    --package acme.commerce --client-class CommerceClient --check
```

`--check` exits non-zero if any base file would change, any drift
warning fires, or any stale base file would be pruned.

## Useful flags

| Flag | What it does |
| --- | --- |
| `--rules path/to/rules.yaml` | Project-local override layer (see [Rules and extensions](rules.md)). |
| `--strip-prefix /api/v1` | Drop a base prefix from every path before classification. |
| `--lang en` | ISO language code for NLP (defaults to `en`). |
| `--nlp-cache-dir DIR` | Where to look for / store the spaCy model. |
| `--shape models\|dicts` | Lock the generated client to one [response shape](shapes.md). Omit for the dual-shape default (constructor `shape=` + `with_shape()`). |
| `--templates-dir DIR` | Override packaged Jinja templates per project (see [Templates](templates.md)). |
| `--model-templates-dir DIR` | Override `datamodel-code-generator`'s model templates. |
| `--check` | CI dry-run: report drift, exit non-zero on any change. |
| `--quiet` / `-q` | Suppress drift-detection warnings (pruning still runs). |
| `-v` / `-vv` | INFO / DEBUG logging. |
