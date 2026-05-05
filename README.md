<p align="center">
  <img src="https://raw.githubusercontent.com/ffaraone/okapipy/main/assets/logo.png" alt="okapipy" width="220" />
</p>

<h1 align="center">okapipy</h1>

[![CI](https://github.com/ffaraone/okapipy/actions/workflows/ci.yml/badge.svg)](https://github.com/ffaraone/okapipy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/okapipy.svg)](https://pypi.org/project/okapipy/)
[![Python versions](https://img.shields.io/pypi/pyversions/okapipy)](https://pypi.org/project/okapipy/)
[![License](https://img.shields.io/pypi/l/okapipy)](https://github.com/ffaraone/okapipy/blob/main/LICENSE)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ffaraone_okapipy)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=coverage)](https://sonarcloud.io/component_measures?id=ffaraone_okapipy&metric=coverage)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ffaraone_okapipy&metric=sqale_rating)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A Python OpenAPI client generator that lifts the flat list of paths in an
OpenAPI 3.x document into a hierarchical tree of **Namespaces**, **Collections**,
**Resources**, **Singletons**, and **Actions**, and emits a strongly-typed,
async/sync Pydantic v2 client from it.

## Installation

okapipy requires Python 3.12+ and uses [uv](https://docs.astral.sh/uv/) for
dependency management.

```bash
uv add okapipy            # add to an existing project
# or, for one-off use:
uvx okapipy --help
```

The first NLP-dependent run downloads the spaCy `en_core_web_sm` model
(~12 MB) into `./.spacy/`. To pre-warm it:

```bash
uv run okapipy nlp fetch en
```

## Usage

Parse a spec into its structural tree (path or http(s) URL accepted):

```bash
uv run okapipy spec parse openapi.yaml --output tree.yaml
```

Generate a full client project:

```bash
uv run okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient
```

This writes a complete Python project under `./my-client` with a
regeneratable base layer (`src/acme/commerce/base/...`) and a one-shot user
layer of subclass stubs you can safely customize. Re-running the command
refreshes the base layer while preserving your edits in the user layer.

Useful flags:

- `--rules path/to/rules.yaml` — project-local overrides for namespace
  assignment, segment kind, and operation exclusion (mirrors the
  `x-okapipy-*` extensions; rules-file values win on conflict).
- `--strip-prefix /api/v1` — drop a base prefix from every path before
  classification.
- `--no-models` (alias `--without-models`) — skip emitting `base/models.py`
  and drop every model import from the generated client. Operations end up
  untyped (raw dicts in / out). Useful when `datamodel-code-generator`
  can't process the spec's schemas, or when the consumer prefers to bring
  their own types.
- `--check` — CI dry-run: report drift and stale files, exit non-zero on
  any change.

## How paths become a tree

okapipy walks each OpenAPI path one segment at a time and assigns each segment
one of five kinds:

| Kind          | What it represents                                  | Example path                          |
| ------------- | --------------------------------------------------- | ------------------------------------- |
| **Namespace** | A folder-style grouping (no operations of its own)  | `/commerce/...`, `/auth/...`          |
| **Collection**| A plural endpoint that lists/creates                | `/orders`, `/users`                   |
| **Resource**  | A single item within a collection (after `{id}`)    | `/orders/{id}`                        |
| **Singleton** | A resource with no enclosing collection             | `/me`, `/health`, `/users/{id}/avatar`|
| **Action**    | A non-CRUD verb endpoint                            | `/login`, `/orders/{id}/submit`       |

Classification runs in this order: `{id}`-shaped segments are resources,
explicit `x-okapipy-kind` hints win next, then the namespace registry, then
spaCy POS/morphology. When everything else is silent the segment defaults to
a collection.

HTTP methods are routed to fixed slots:

| Terminal kind | GET        | POST     | PUT      | PATCH            | DELETE   |
| ------------- | ---------- | -------- | -------- | ---------------- | -------- |
| Collection    | `fetch`    | `create` | dropped  | dropped          | dropped  |
| Resource      | `retrieve` | dropped  | `update` | `partial_update` | `delete` |
| Singleton     | `retrieve` | dropped  | `update` | `partial_update` | `delete` |
| Action        | appended to `Action.operations` (one Action holds every method on its path) |

Operations that don't fit (e.g. `POST /orders/{id}` without an action hint)
are dropped with a warning rather than coerced into something synthetic — opt
them in with `x-okapipy-kind: action` if you want them.

## OpenAPI extensions

You decorate the spec with `x-okapipy-*` keys to override the heuristics. All
extensions are optional; small/well-named specs often need none.

### `x-okapipy-ns` (root level)

Declares which top-level path segments are folders. Without this, a singular
noun like `commerce` or `auth` would be classified as a namespace only when
the heuristic guesses correctly — the registry makes it deterministic.

```yaml
openapi: 3.0.0
info:
  title: Commerce API
  version: 1.0.0
x-okapipy-ns:
  - commerce
  - commerce/internal
  - settings
paths:
  /commerce/orders:           # → Namespace(commerce) → Collection(Orders)
    get: ...
  /commerce/internal/audit:   # nested namespaces are fine
    get: ...
```

Leading slashes are tolerated (`/commerce` and `commerce` are equivalent).

### `x-okapipy-kind` (path-item or operation level)

Forces a segment's classification when NLP can't disambiguate. Allowed values:
`namespace`, `collection`, `singleton`, `action`.

```yaml
paths:
  /me:
    x-okapipy-kind: singleton  # /me is a Singleton, not a Namespace
    get:
      summary: Return the current user
    patch:
      summary: Update the current user

  /orders/{id}/submit:
    post:
      x-okapipy-kind: action   # operation-level: route this POST to a synthetic Action

  /staff:                      # spaCy treats "staff" as singular → namespace by default
    x-okapipy-kind: collection
    get: ...
```

Path-item-level hints classify the *segment* (and propagate to other paths
that walk through the same prefix — declaring `/me` as a singleton once is
enough for `/me/notifications`, `/me/refresh`, etc., to see it). Operation-level
`x-okapipy-kind: action` is narrower: it routes a single method to a
synthetic Action without changing the segment's classification.

### `x-okapipy-exclude` (path-item level)

Skips operations during parsing — useful for endpoints you don't want in the
generated client (admin-only, deprecated migration endpoints, internal
debugging routes).

```yaml
paths:
  /internal/debug:
    x-okapipy-exclude: "*"           # drop every method on this path

  /orders/{id}:
    x-okapipy-exclude: [DELETE]      # drop just DELETE; keep GET / PUT / PATCH
    get: ...
    delete: ...
```

Method names are case-insensitive.

### `x-okapipy-paginated` (path-item or operation level)

Overrides whether a list-shaped operation is treated as paginated by the
generator. Defaults to `true`. Set it to `false` for endpoints that return a
bounded list (e.g. enum-like reference data).

```yaml
paths:
  /currencies:
    x-okapipy-paginated: false       # the full list is short and fixed
    get:
      responses:
        '200':
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: '#/components/schemas/Currency'
```

## Singletons

A **Singleton** is a resourceful endpoint with no enclosing collection: there
is exactly one of it, and CRUD verbs apply directly to it. Common cases:

- `/me`, `/self` — the authenticated user
- `/health`, `/status`, `/version` — service introspection
- `/users/{id}/avatar` — a sub-singleton owned by a parent resource

Singletons must opt in with `x-okapipy-kind: singleton`. Without the hint, a
single-noun segment like `me` would be classified as a Namespace because
spaCy can't tell the two apart from the segment alone. Once declared, the
Singleton accepts the same CRUD verbs as a Resource (`GET → retrieve`,
`PUT → update`, `PATCH → partial_update`, `DELETE → delete`) and may host
nested collections, sub-singletons, or actions.

```yaml
paths:
  /me:
    x-okapipy-kind: singleton
    get: { summary: Return the current user, responses: { '200': ... } }
    patch: { summary: Update the current user, responses: { '200': ... } }

  /users/{id}/avatar:
    x-okapipy-kind: singleton    # sub-singleton under the User resource
    parameters: [{ name: id, in: path, required: true, schema: { type: string } }]
    get: ...
    put: ...
    delete: ...
```

The generated client surfaces these as direct attributes — `client.me.retrieve()`,
`client.users(id).avatar.update(...)` — rather than going through a
collection.

## Root and namespace-level actions

Verb endpoints can attach at the root of the API or directly under a
namespace. Plenty of real-world specs do this (`/login`, `/logout`,
`/password-reset`, `/auth/refresh`), and okapipy does not require a wrapper
collection for them.

```yaml
paths:
  /login:
    post: ...                         # → APIModel.actions[Login]
  /password-reset:
    post: ...                         # multi-token verb-phrase → Action
  /auth/refresh:
    post: ...                         # → Namespace(auth).actions[Refresh]
```

Detection notes:

- **Multi-token kebab segments** with a non-plural head (e.g. `password-reset`,
  `force-reimport`) are detected as verb-phrases by spaCy directly.
- **Single-token API verbs** that small spaCy models mistag as nouns (`login`,
  `logout`, `refresh`, `revoke`, `verify`, `subscribe`, `unsubscribe`,
  `activate`, `deactivate`, `enable`, `disable`, `archive`, `publish`, `ping`,
  …) are caught by an English-language registry.
- Anything outside that set should use `x-okapipy-kind: action` to be safe.

Naming follows the breadcrumb of singular collection names — namespaces don't
contribute. So `/login` becomes `Login`, `/auth/refresh` becomes `Refresh`,
and `/users/{id}/avatar` becomes `UserAvatar`. Cross-namespace name
collisions (e.g. `/web/login` and `/admin/login` would both be `Login`) are
left to the generator to disambiguate.

## Rules file

The rules file is a **project-local override layer**. Use it when you don't
own the OpenAPI document but still need to fix classifications, exclude
endpoints, or declare namespaces. It mirrors the spec extensions; rules-file
values win on every conflict.

Pass it via `--rules`:

```bash
uv run okapipy spec parse openapi.yaml --rules okapipy.rules.yaml
```

The file is local-only (no URLs) and accepts JSON or YAML:

```yaml
# Same shape as x-okapipy-ns at the root of the spec.
x-okapipy-ns:
  - commerce
  - commerce/internal
  - settings

paths:
  # Path-item-level kind override (mirrors x-okapipy-kind on a path).
  /staff:
    x-okapipy-kind: collection

  # Force /me to be a singleton even though the upstream spec is silent.
  /me:
    x-okapipy-kind: singleton

  # Per-method override (mirrors x-okapipy-kind on an operation).
  /orders/{id}/submit:
    post:
      x-okapipy-kind: action

  # Per-method pagination override.
  /currencies:
    x-okapipy-paginated: false

  # Drop every method on a path…
  /internal/debug:
    x-okapipy-exclude: "*"

  # …or just selected methods.
  /orders/{id}:
    x-okapipy-exclude: [DELETE]
```

Allowed `x-okapipy-kind` values: `namespace`, `collection`, `action`,
`singleton`. Unknown values are rejected at load time with the path that
contains them.
