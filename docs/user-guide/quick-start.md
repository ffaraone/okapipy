# Quick start

This page walks you from "I have an OpenAPI document" to "I have a typed
Python client I can `import` and call." Five minutes, three commands.

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
okapipy parse openapi.yaml
```

You'll get:

* a counts panel on stderr (namespaces, collections, resources,
  singletons, actions),
* a JSON dump of the parsed tree on stdout — pipe it into `less` or
  `jq`.

If you'd rather save the tree:

```bash
okapipy parse openapi.yaml --output tree.yaml
# .yaml or .yml → YAML; .json → JSON. Anything else errors.
```

If a classification looks off (`Staff` should be a collection, `me`
should be a singleton, a non-CRUD POST got dropped), fix it in the rules
file — see [Rules and extensions](rules.md) — before generating.

## 3. Scaffold the project manifest

`okapipy generate` is manifest-driven: every project-level setting
(package name, client class, response shape) and every per-spec setting
(source, rules, strip-prefix) lives in a small YAML file. Scaffold one
with `okapipy init`:

```bash
okapipy init openapi.yaml \
    --package acme.commerce --client-class CommerceClient
```

That writes `./okapipy.yml`:

```yaml
# okapipy project manifest. See https://ffaraone.github.io/okapipy/ for details.

# Required.
package: acme.commerce
client_class: CommerceClient

# Project metadata — drives pyproject.toml, LICENSE, and README.
project_name: commerce
project_description: Generated client for commerce
project_version: "0.1.0"
python_version: "3.13"
license: Proprietary  # SPDX id (MIT, Apache-2.0, BSD-3-Clause, MPL-2.0, ...)
author: Your Organization
repo_url: https://github.com/your-org/your-repo

# Optional generation settings — see the manifest reference for details.
# shape: auto  # auto | models | dicts
# output: ./out

specs:
  - namespace: ''
    source: openapi.yaml
```

Edit the `author` and `repo_url` lines (and the `license` if you have
one in mind), commit the file alongside your consumer code, and you're
done — it's the source of truth for what the generated client looks
like.

## 4. Generate a client

```bash
okapipy generate --output ./my-client
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
│   ├── collections/        # one-shot — your subclasses live here
│   ├── resources/
│   ├── actions/
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
│       └── _generated.json
└── tests/                  # one-shot scaffolding
```

Two layers, two lifecycles: the `base/` tree is rewritten on every
`okapipy generate`, the rest is emitted **once** and then yours
forever. See [Code customization](customization.md) for the full story.

!!! tip
    Add `output: ./my-client` to `okapipy.yml` and you can drop the
    `--output` flag too — subsequent runs are just
    `okapipy generate`.

## 5. Use it

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

## 6. Iterate

When the upstream spec changes, re-run the same command:

```bash
okapipy generate
```

The `base/` tree is rewritten to match the new spec. Your subclasses,
your `client.py`, your `__init__.py` — all left strictly alone. If the
spec gained a new namespace or collection, the generator emits new
*one-shot* stubs for it and prints a drift-detection warning telling
you which factory line to add to wire your subclass into the tree.

In CI, gate on regeneration being committed:

```bash
okapipy generate --check
```

`--check` exits non-zero if any base file would change, any drift
warning fires, or any stale base file would be pruned.

## The project manifest

Every option used to drive generation lives in `okapipy.yml`. A typical
one for a single-spec project:

```yaml
package: acme.commerce              # required: dotted Python package
client_class: CommerceClient        # required: PascalCase
output: ./out                       # optional: --output overrides this

# Optional project metadata — populates pyproject.toml + LICENSE.
project_description: Acme commerce API client
license: Apache-2.0
author: Acme Corp
repo_url: https://github.com/acme/commerce

specs:                              # required: at least one entry
  - namespace: ''                   # '' mounts at the package root
    source: ./openapi.yaml          # path or http(s) URL
    rules: ./rules.yaml             # optional, local path only
```

Paths are resolved relative to the manifest file's directory, so the
manifest is portable with the consumer repo. URLs (`source:
https://…`) are left verbatim.

For every field the manifest accepts — version, Python, response
shape, templates, per-spec overrides — see the [manifest
reference](../reference/manifest.md).

### Multi-spec projects

Microservice projects can mount several OpenAPI documents under one
client by adding more entries to `specs:`. Each entry pairs a spec
source with its own namespace, rules, and prefix-stripping rules;
callers reach each service as a top-level accessor on the client
(`client.auth.*`, `client.restapi.*`). See
[Advanced usage](advanced.md) for the full pattern.

## Useful CLI flags

| Flag | What it does |
| --- | --- |
| `--manifest PATH` | Read a manifest from somewhere other than `./okapipy.yml`. |
| `--output DIR`, `-o DIR` | Override the manifest's `output` field. |
| `--check` | CI dry-run: report drift, exit non-zero on any change. |
| `--quiet` / `-q` | Suppress drift-detection warnings (pruning still runs). |
| `-v` / `-vv` | INFO / DEBUG logging. |
