<p align="center">
  <img src="https://raw.githubusercontent.com/ffaraone/okapipy/main/assets/logo.png" alt="okapipy" width="220" />
</p>

<h1 align="center">okapipy</h1>

[![CI](https://github.com/ffaraone/okapipy/actions/workflows/ci.yml/badge.svg)](https://github.com/ffaraone/okapipy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/okapipy.svg)](https://pypi.org/project/okapipy/)
[![Python versions](https://img.shields.io/pypi/pyversions/okapipy)](https://pypi.org/project/okapipy/)
[![License](https://img.shields.io/pypi/l/okapipy)](https://github.com/ffaraone/okapipy/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-ffaraone.github.io%2Fokapipy-5b3621?labelColor=2c1a10)](https://ffaraone.github.io/okapipy/)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ffaraone_okapipy)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=coverage)](https://sonarcloud.io/component_measures?id=ffaraone_okapipy&metric=coverage)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ffaraone_okapipy&metric=sqale_rating)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

okapipy turns an OpenAPI 3.x document into a typed, hierarchical Python
client — sync **and** async, sharing one tree. It lifts flat paths into
**Namespaces**, **Collections**, **Resources**, **Singletons**, and
**Actions**, then emits a project where regenerated `base/` code carries
the wiring and a one-shot user layer carries your customizations. Re-run
the generator after a spec change and your code is left strictly alone.

📚 **Full documentation:** <https://ffaraone.github.io/okapipy/>

## Install

okapipy needs Python 3.12+.

```bash
pip install okapipy            # or: uv add okapipy
```

The first NLP-dependent run downloads the spaCy `en_core_web_sm` model
(~12 MB) into `./.spacy/`. To pre-warm it (recommended in CI):

```bash
okapipy nlp fetch en
```

## Use

Sanity-check the parse:

```bash
okapipy spec parse openapi.yaml
```

Generate a full client project:

```bash
okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient
```

This writes a runnable Python project under `./my-client`: a regenerated
`src/acme/commerce/base/` tree (transport, models, vendored runtime,
per-node base classes) plus one-shot subclass stubs you can customize.
Re-running the command refreshes `base/` and leaves your edits alone.

Useful flags: `--rules path/to/rules.yaml` for project-local overrides,
`--strip-prefix /api/v1` to drop a base prefix, `--no-models` to skip
emitting `base/models.py` (operations end up untyped), `--check` for a CI
dry-run that exits non-zero on any drift.

## Customize

okapipy decides what each path segment is using POS tagging plus a small
heuristic registry. When the heuristics get it wrong — or when you need
to fence off pieces of the spec — you decorate the spec with `x-okapipy-*`
extensions, or carry the same overrides in a project-local **rules file**
(useful when you don't own the OpenAPI document). Rules-file values win
on every conflict.

Pass a rules file with `--rules`:

```bash
okapipy spec generate openapi.yaml --rules okapipy.rules.yaml \
    --output ./my-client --package acme.commerce --client-class CommerceClient
```

### OpenAPI extensions

```yaml
openapi: 3.0.0
info: { title: Commerce API, version: 1.0.0 }

# Declare top-level folders so single-noun segments aren't misclassified.
x-okapipy-ns:
  - commerce
  - commerce/internal
  - settings

paths:
  /commerce/orders:                      # Plural → Collection
    get: ...
    post: ...

  /commerce/orders/{id}/submit:          # Verb → Action (NLP catches it)
    post: ...

  /me:
    x-okapipy-kind: singleton            # Force singleton; NLP can't tell apart from a namespace
    get: ...
    patch: ...

  /staff:
    x-okapipy-kind: collection           # spaCy thinks "staff" is singular — override

  /currencies:
    x-okapipy-paginated: false           # Override pagination heuristic
    get: ...

  /internal/debug:
    x-okapipy-exclude: "*"               # Drop every method on this path

  /orders/{id}:
    x-okapipy-exclude: [DELETE]          # Or just selected methods
    get: ...
    delete: ...
```

Allowed `x-okapipy-kind` values: `namespace`, `collection`, `singleton`,
`action`. Path-item hints classify the *segment* and propagate to other
paths walking through the same prefix. Operation-level
`x-okapipy-kind: action` is narrower — it routes a single method to a
synthetic Action without changing the segment classification.

### Rules file

The rules file mirrors the spec extensions and is local-only (no URLs).
JSON or YAML, same shape:

```yaml
x-okapipy-ns:
  - commerce
  - settings

paths:
  /staff:
    x-okapipy-kind: collection
  /me:
    x-okapipy-kind: singleton
  /orders/{id}/submit:
    post:
      x-okapipy-kind: action
  /currencies:
    x-okapipy-paginated: false
  /internal/debug:
    x-okapipy-exclude: "*"
  /orders/{id}:
    x-okapipy-exclude: [DELETE]
```

For more — code customization, custom strategies, template overrides,
client construction options — see <https://ffaraone.github.io/okapipy/>.

## Using the generated client

```python
from acme.commerce import CommerceClient
import httpx

with CommerceClient(
    base_url="https://api.example.com",
    auth=httpx.BearerAuth("..."),
) as client:
    # Iterate a collection — pagination is automatic.
    for order in client.commerce.orders:
        print(order.id, order.total)

    # Filter, sort, page-size — fluent and order-independent.
    for order in (
        client.commerce.orders
        .filter(status="open")
        .order_by("-created_at")
        .page_size(50)
    ):
        ...

    # Resource lookup uses [id], not (id). Indexing is request-free;
    # .retrieve() issues the GET.
    order = client.commerce.orders["ord_42"].retrieve()

    # Sub-collections walk the tree naturally.
    line = client.commerce.orders["ord_42"].lines.create(
        body={"sku": "SKU-1", "qty": 2},
    )

    # Actions return an action object whose method is `.run()`.
    client.commerce.orders["ord_42"].submit.run()

    # Singletons (e.g. /me, /health) are direct attributes.
    me = client.me.retrieve()

    # Count without a full walk.
    total = client.commerce.orders.filter(status="open").count()
```

Async is the same shape, `Async`-prefixed:

```python
from acme.commerce import AsyncCommerceClient

async with AsyncCommerceClient(base_url="https://api.example.com") as client:
    async for order in client.commerce.orders:
        ...
    me = await client.me.retrieve()
```

## Contributing

Bug reports, feature requests, and pull requests are welcome via
[GitHub issues](https://github.com/ffaraone/okapipy/issues) and
[pull requests](https://github.com/ffaraone/okapipy/pulls).

To work on okapipy locally:

```bash
git clone https://github.com/ffaraone/okapipy.git
cd okapipy
uv sync
uv run okapipy nlp fetch en              # one-time spaCy model download
uv run pytest                            # full suite + coverage
uv run mypy src/okapipy/parser           # strict type-check (parser)
uv run ruff check src tests              # lint
```

Before opening a PR, please ensure tests, type-check, and lint all pass.
The internal design notes live under [`design/`](design/) — read the
relevant spec and plan before changing parser, generator, or
customization behavior. Coding and documentation conventions are in
[`CLAUDE.md`](CLAUDE.md).

## License

okapipy is released under the [Apache License 2.0](LICENSE). You're free
to use it commercially, modify it, and redistribute it; please keep the
copyright notice and the license text intact.
