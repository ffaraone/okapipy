# Response shape

A *shape* decides how the generated client deserializes responses and
types its method signatures. okapipy supports three shapes, chosen once
at generation time with the `--shape` flag of `okapipy spec generate`:

- **`auto`** (the default — pass nothing): emit a dual-shape client.
  Each call site picks a shape; flip it at runtime with
  `with_shape(...)`.
- **`--shape models`**: lock the client to typed Pydantic models.
  Method signatures are narrow; the runtime switch is gone.
- **`--shape dicts`**: lock the client to raw dicts. `base/models.py`
  is not emitted.

The choice is per generation, not per call. Switching shape after the
fact means re-running `okapipy spec generate`.

## Pick a shape

| | `auto` (default) | `--shape models` | `--shape dicts` |
|---|---|---|---|
| `base/models.py` | emitted | emitted | **not** emitted |
| Constructor `shape=` keyword | yes | no | no |
| `with_shape(...)` method | yes | no | no |
| Body parameter type | `Foo \| dict[str, Any]` | `Foo` | `dict[str, Any]` |
| Return type | `Foo \| dict[str, Any] \| None` | `Foo \| None` | `dict[str, Any] \| None` |
| Iterator item type | `Foo \| dict[str, Any]` | `Foo` | `dict[str, Any]` |

`Foo` stands in for the Pydantic model okapipy recovered from the spec.
When the parser cannot recover a schema name (inline body, anonymous
response, primitive alias), the corresponding type falls back to
`dict[str, Any]` — okapipy does not fabricate model names.

## The default: dual shape

Omit `--shape` and the generator emits a client where each call site
chooses its shape at construction time:

```bash
$ okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient
```

The constructor takes a `shape` keyword (default `"models"`):

=== "Sync"

    ```python
    from acme.commerce import CommerceClient

    typed = CommerceClient(base_url="https://api.example.com", shape="models")
    raw   = CommerceClient(base_url="https://api.example.com", shape="dicts")

    typed_order = typed.commerce.orders["ord_42"].retrieve()
    # → Order(id='ord_42', total='99.00', status='open')

    raw_order = raw.commerce.orders["ord_42"].retrieve()
    # → {'id': 'ord_42', 'total': '99.00', 'status': 'open'}
    ```

=== "Async"

    ```python
    from acme.commerce import AsyncCommerceClient

    typed = AsyncCommerceClient(base_url="https://api.example.com", shape="models")
    raw   = AsyncCommerceClient(base_url="https://api.example.com", shape="dicts")

    typed_order = await typed.commerce.orders["ord_42"].retrieve()
    raw_order   = await raw.commerce.orders["ord_42"].retrieve()
    ```

`with_shape(...)` returns a sibling client that shares the same
`httpx.Client`, transport, and strategies — no second connection pool,
no auth re-init:

=== "Sync"

    ```python
    typed = CommerceClient(base_url="https://api.example.com")  # default shape="models"
    raw   = typed.with_shape("dicts")

    assert raw._http is typed._http   # same connection pool, same auth
    ```

=== "Async"

    ```python
    typed = AsyncCommerceClient(base_url="https://api.example.com")
    raw   = typed.with_shape("dicts")

    assert raw._http is typed._http
    ```

In `auto` mode every body / return type carries both arms
(`Foo | dict[str, Any]` / `Foo | dict[str, Any] | None`) so callers can
mix without the type checker complaining.

Reach for `auto` when you want models in tests and dicts in scripts (or
the other way around) without maintaining two generated packages — and
when the wider type signatures are an acceptable tradeoff.

## `--shape models`: typed only

```bash
$ okapipy spec generate openapi.yaml ... --shape models
```

The constructor accepts no `shape=` keyword. `with_shape(...)` is not
emitted. Method signatures are narrow:

```python
def retrieve(self, **overrides: Any) -> Order | None: ...
def update(self, body: Order, **overrides: Any) -> Order | None: ...
```

```python
from acme.commerce import CommerceClient

client = CommerceClient(base_url="https://api.example.com")
order = client.commerce.orders["ord_42"].retrieve()
# → Order(id='ord_42', total='99.00')

assert isinstance(order, Order)
```

When the parser couldn't recover a schema name for a particular
operation, that operation's body or return falls back to
`dict[str, Any]` — even in models mode. okapipy never invents type
names.

Reach for `models` when you want strict types end-to-end, run `mypy` on
user code, or care about IDE completion at every call site.

## `--shape dicts`: raw dicts only

```bash
$ okapipy spec generate openapi.yaml ... --shape dicts
```

`base/models.py` is **not** emitted, every model import is dropped from
the generated tree, and every body / return is typed `dict[str, Any]`:

```python
def retrieve(self, **overrides: Any) -> dict[str, Any] | None: ...
def update(self, body: dict[str, Any], **overrides: Any) -> dict[str, Any] | None: ...
```

```python
from acme.commerce import CommerceClient

client = CommerceClient(base_url="https://api.example.com")
order = client.commerce.orders["ord_42"].retrieve()
# → {'id': 'ord_42', 'total': '99.00'}
```

`datamodel-code-generator` is never invoked, so generation is faster
and the generated tree is one file smaller.

Reach for `dicts` when:

- `datamodel-code-generator` can't process the spec's schemas (deeply
  nested `oneOf` graphs occasionally break it).
- You wrap responses in your own types and the generated Pydantic
  layer would just be marshalling overhead.
- You want the smallest possible generated tree for an ad-hoc script.

## Two flavors in one project

okapipy emits source under `src/<package_path>/` and tests under
`tests/<package_path>/`, where `<package_path>` mirrors `--package`.
That means you can generate two flavors of the same spec into one
project — one typed, one raw — by varying `--package`:

```bash
# Models-flavored client at acme.commerce.models.client.CommerceClient.
$ okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce.models \
    --client-class CommerceClient \
    --project-name acme-commerce \
    --shape models

# Dicts-flavored client at acme.commerce.dicts.client.CommerceClient.
$ okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce.dicts \
    --client-class CommerceClient \
    --project-name acme-commerce \
    --shape dicts
```

The second run leaves the one-shot project skeleton
(`pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`,
`.python-version`) untouched and writes its own
`src/acme/commerce/dicts/...` tree plus its own
`tests/acme/commerce/dicts/...` tree alongside the first flavor.

```python
from acme.commerce.models import CommerceClient as TypedClient
from acme.commerce.dicts import CommerceClient as RawClient
```

A few things to know:

- **Pass the same `--project-name` to both runs.** `--project-name`
  defaults to the last segment of `--package`, so without it the first
  run sets the project name to `models` and the second to `dicts`. The
  pyproject is one-shot, so only the first wins.
- **Edit `--cov` by hand.** The generated `pyproject.toml` sets
  `--cov=<package>` from the first run. Change it to
  `--cov=acme.commerce` if you want coverage across both flavors.
- **Drift detection is per flavor.** Each flavor has its own
  `base/_manifest.json`. `--check` and pruning don't see across
  flavors; running `--check` on one flavor never reports drift in the
  other.

## Choosing a shape

A short opinionated guide, when in doubt:

- **`auto`** if you don't know yet. The runtime switch costs one extra
  arm in the type signature and buys you per-call-site flexibility.
- **`--shape models`** for production code that runs `mypy --strict`
  and benefits from precise types.
- **`--shape dicts`** when the spec resists
  `datamodel-code-generator`, or when models would just be marshalling
  overhead in your hot path.

If you want both worlds in one repo, use the two-flavors recipe above.

## See also

- [Quick start](quick-start.md) — generate your first client.
- [Using the client](client-usage.md) — the surface common to all three shapes.
- [Code customization](customization.md) — the two-layer split that makes regeneration safe.
- [CLI reference](../reference/cli.md#okapipy-spec-generate) — every flag of `okapipy spec generate`.
