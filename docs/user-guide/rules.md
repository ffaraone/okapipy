# Rules and extensions

okapipy gets a long way by reading the spec — plurals are collections,
verbs are actions, `{id}` segments are resources. When that's not enough
(or when you don't own the spec), there are two override layers:

1. **`x-okapipy-*` extensions** baked into the spec itself, and
2. **A project-local rules file** that mirrors the same shape and wins on
   conflict.

The rules file is the right tool when you can't (or shouldn't) edit the
upstream OpenAPI document. The extensions are the right tool when you
*do* own the spec and want the hint to live next to the path it
classifies.

## Classification, in order

For each path segment, okapipy checks:

1. Is it `{something}`? → **resource identifier**.
2. Is there an explicit `x-okapipy-kind` (rules file first, then spec)?
   → use it.
3. Is the segment listed in the namespace registry? → **namespace**.
4. What does spaCy say? Plural noun → **collection**; verb (or
   verb-phrase) → **action**.
5. Fallback → **collection**, with a warning.

Allowed `x-okapipy-kind` values: `namespace`, `collection`, `action`,
`singleton`. Unknown values are rejected at load time, with the path that
contains them.

## `x-okapipy-ns` — declare your folders

`x-okapipy-ns` is a **root-level** list of path prefixes that should be
treated as namespaces. Without it, a singular noun like `commerce` or
`auth` only becomes a namespace when the heuristic guesses correctly —
the registry makes it deterministic.

```yaml
openapi: 3.0.0
info:
  title: Commerce API
  version: 1.0.0
x-okapipy-ns:
  - commerce
  - commerce/internal     # nested namespaces are fine
  - settings

paths:
  /commerce/orders: { ... }            # → Namespace(commerce) → Collection(Orders)
  /commerce/internal/audit: { ... }    # → ns(commerce) → ns(internal) → Collection(Audit)
```

Leading slashes are tolerated — `/commerce` and `commerce` are the same
thing.

## `x-okapipy-kind` — override a single segment

Two scopes:

* **Path-item level** — classifies the *segment itself*, and propagates to
  any other path that walks through the same prefix.
* **Operation level** — narrower; routes a *single method* to a synthetic
  Action without changing the segment's classification.

```yaml
paths:
  /me:
    x-okapipy-kind: singleton          # /me is a Singleton, not a Namespace
    get:    { summary: Return the current user, responses: { '200': ... } }
    patch:  { summary: Update the current user, responses: { '200': ... } }

  /orders/{id}/submit:
    post:
      x-okapipy-kind: action           # operation-level: route this POST to an Action

  /staff:                              # spaCy treats "staff" as singular → Namespace
    x-okapipy-kind: collection
    get: { ... }
```

Declaring `/me` as a singleton once is enough — `/me/notifications`,
`/me/refresh`, etc. will all see it.

## `x-okapipy-exclude` — skip operations

Path-item level. Drops operations during parsing, useful for endpoints
that shouldn't appear in the generated client:

```yaml
paths:
  /internal/debug:
    x-okapipy-exclude: "*"             # drop every method on this path

  /orders/{id}:
    x-okapipy-exclude: [DELETE]        # drop just DELETE; keep GET / PUT / PATCH
    get:    { ... }
    delete: { ... }
```

Method names are case-insensitive.

## `x-okapipy-paginated` — opt out of pagination

Three scopes — document root, path-item, or operation. Defaults to `true`;
set it to `false` for list-shaped endpoints that return a bounded list
(enum-like reference data, configuration, etc.).

Precedence, narrowest first: operation → path-item → document root → default
`true`. Within each scope, a rules-file value wins over the spec value.

!!! note "What the generated collection looks like"
    Setting `x-okapipy-paginated: false` is not just a hint — the
    generator emits a different collection class. `page_size(n)` and
    `get_page(n)` are dropped, the iterator class is dropped, and
    `count()` / `first()` / `exists()` / iteration all collapse to a
    single GET that never consults the pagination strategy. See
    [Using the client → Non-paginated collections](client-usage.md#non-paginated-collections)
    for the user-facing shape.

```yaml
paths:
  /currencies:
    x-okapipy-paginated: false         # the full list is short and fixed
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

Use the **root-level** form when the API as a whole isn't paginated and
you don't want to repeat the flag on every collection:

```yaml
openapi: 3.0.0
info:
  title: Reference Data API
  version: 1.0.0
x-okapipy-paginated: false             # disable pagination by default

paths:
  /currencies:    { get: { ... } }     # non-paginated
  /countries:     { get: { ... } }     # non-paginated
  /transactions:
    x-okapipy-paginated: true          # this one is paginated
    get: { ... }
```

The same key works at the root of a rules file:

```yaml
# okapipy.rules.yaml
x-okapipy-paginated: false
paths:
  /transactions:
    x-okapipy-paginated: true
```

## The rules file

When you can't edit the upstream spec, write the same hints in a JSON or
YAML file and point `--rules` at it. **Rules-file values win** on every
conflict with the spec.

```yaml
# okapipy.rules.yaml — same shape as the extensions, lifted to the root.
x-okapipy-ns:
  - commerce
  - commerce/internal
  - settings

paths:
  /staff:
    x-okapipy-kind: collection         # path-item-level kind override

  /me:
    x-okapipy-kind: singleton          # force /me to be a singleton

  /orders/{id}/submit:
    post:
      x-okapipy-kind: action           # per-method kind override

  /currencies:
    x-okapipy-paginated: false         # per-path pagination override

  /internal/debug:
    x-okapipy-exclude: "*"             # drop every method

  /orders/{id}:
    x-okapipy-exclude: [DELETE]        # drop just selected methods
```

Then point your project manifest's `specs[]` entry at the rules file
(`okapipy parse` keeps its own `--rules` flag for ad-hoc
inspection):

```yaml
# okapipy.yml
package: acme.commerce
client_class: CommerceClient
specs:
  - namespace: ''
    source: openapi.yaml
    rules: okapipy.rules.yaml
```

```bash
okapipy parse openapi.yaml --rules okapipy.rules.yaml  # inspection
okapipy generate --output ./my-client                  # uses the manifest
```

The rules file is **local-only** — no http(s) URLs. That's a deliberate
constraint; project-local overrides should live in your repo, not on a
remote server you don't control.

## Worked examples

### "spaCy keeps tagging my collection as a namespace"

The token looks singular to spaCy — typically because the lemma matches a
common singular noun in English (`staff`, `news`, `series`, `species`).
Force it:

```yaml
# rules.yaml
paths:
  /staff:
    x-okapipy-kind: collection
```

### "I want a custom verb to be an Action"

If the verb isn't in the small built-in registry (`login`, `logout`,
`refresh`, `revoke`, `verify`, …), spaCy will probably tag it as a noun.
Pick the smallest possible scope:

```yaml
paths:
  /orders/{id}/escalate:
    post:
      x-okapipy-kind: action     # operation-level: just this POST
```

If every method on the path is the action (rare), use path-item level:

```yaml
paths:
  /orders/{id}/escalate:
    x-okapipy-kind: action
    post: { ... }
    get:  { ... }                # also routed to Action.operations
```

### "I have many non-conforming endpoints and I don't want to annotate each one"

Some specs are full of operations that don't fit the
namespace/collection/resource/singleton/action hierarchy — `PUT /users`
for bulk updates, `GET /admin/health`, one-off RPCs that share a
collection path with regular CRUD. By default each one is dropped with
a warning, and the per-op fix is `x-okapipy-kind: action` on every one
of them.

When that's impractical — usually because you don't own the spec, or
there are too many — add `unmatched: <name>` to the spec entry in your
manifest to keep them as flat actions under a synthetic top-level
namespace:

```yaml
# okapipy.yml
specs:
  - namespace: ''
    source: openapi.yaml
    unmatched: ops
```

```bash
okapipy generate --output ./my-client
```

Now every otherwise-dropped operation lands under `client.ops` as its
own action, named after its `operationId`:

```python
client.ops.bulk_update_users.run(body={...})   # PUT /users
client.ops.admin_health.run()                  # GET /admin/health
```

Operations without an `operationId` fall back to
`<method>_<sanitized_path>` — e.g. a bare `PUT /users` becomes
`put_users`. Two unmatched ops sharing an `operationId` produce
`bulk_update`, `bulk_update_2`, … and a parser warning so you can clean
up the spec.

The namespace name is **CLI-only** — there's no rules-file key for it,
because the choice is per-invocation, not a shared project setting.
`<name>` must not collide with the snake_case identifier of any
existing top-level node; on collision the generator exits non-zero with
`UnmatchedNamespaceCollisionError` and writes nothing.

!!! tip "Per-op `x-okapipy-kind: action` is still preferred when you own the spec"
    `--unmatched` is the bulk fallback — it puts everything under one
    flat container. When you control the spec and there's a natural
    home for the endpoint (under a collection, a resource, a
    singleton), `x-okapipy-kind: action` keeps the operation in the
    right place in the hierarchy.

### "An endpoint shouldn't be in the client at all"

```yaml
paths:
  /admin/wipe-database:
    x-okapipy-exclude: "*"
```

`*` is also accepted as the literal string `"*"` to drop every method;
otherwise pass an explicit list.

### "This list is bounded and shouldn't paginate"

```yaml
paths:
  /currencies:
    x-okapipy-paginated: false
    get: { ... }
```

The generated `fetch()` returns the full list in a single request.

### "A `*/current` pseudo-resource has its own members and settings"

REST APIs commonly model "the current org" or "the current workspace" as
a singleton with its own sub-collections (`/orgs/current/members`,
`/orgs/current/roles`, `/workspaces/current/tag-keys`). Mark the
pseudo-resource as a singleton — okapipy hangs the sub-collections off
it directly.

```yaml
paths:
  /orgs/current:
    x-okapipy-kind: singleton
  # /orgs/current/members → MembersCollection on the OrgsCurrentSingleton
  # /orgs/current/roles   → RolesCollection on the OrgsCurrentSingleton
```

In the generated client, `client.orgs.current.members` is the collection
and `client.orgs.current.members["usr_1"]` is the per-member resource.

### "An aggregate view on a collection (`/orders/stats`)"

Endpoints like `/orders/stats`, `/datasets/summary`, or
`/secrets/encrypted` are *derived views* of the parent collection — not
one of its items. Mark them as singletons; okapipy attaches them as
sub-singletons of the parent collection.

```yaml
paths:
  /orders/stats:
    x-okapipy-kind: singleton
    get: { ... }
```

The generated client exposes `client.orders.stats.retrieve()` alongside
iteration over the collection itself.

### "An `/.well-known/...` path crashes with an invalid Python identifier"

Path segments beginning with `.` (`.well-known`, `.config`) aren't valid
Python identifiers. okapipy expands a literal `.` to the word `Dot` so
the generated symbol stays valid: `/.well-known/openid-configuration`
becomes `DotWellKnown` (class) and `dot_well_known` (module/attribute).
No rule is needed; this is built in.

The raw segment is preserved on the parsed model and in the runtime URL
template, so HTTP routing keeps hitting `/.well-known/...` correctly.
