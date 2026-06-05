# Advanced usage

The basics — one spec, one client — are covered in
[Quick start](quick-start.md). This page covers the patterns that
come up when you outgrow a single OpenAPI document.

## Multi-spec projects: one client, several services

Microservice projects typically expose several OpenAPI documents under
one umbrella product. okapipy generates a single Python package from
*all of them* and exposes each service as a top-level namespace on the
client. You write:

```python
client = OptscaleClient(base_url="https://api.example.com")
client.auth.tokens.create(body={"email": "...", "password": "..."})
client.restapi.organizations["org_42"].cloud_accounts.create(body={...})
```

and the generator handles the layout, the imports, the
per-service models, and the regeneration semantics.

### The manifest

Declare one entry under `specs:` per service. Each entry pairs a
spec source with the namespace it mounts under, plus its own optional
rules, prefix stripping, and so on:

```yaml
# okapipy.yml
package: ffc.optscale
client_class: OptscaleClient
shape: dicts
output: ./out

specs:
  - namespace: auth
    source: ./specs/auth.yaml
    rules: ./rules/auth.yaml
    strip_prefix: /auth/v2
  - namespace: restapi
    source: ./specs/restapi.yaml
    rules: ./rules/restapi.yaml
    strip_prefix: /restapi/v2
```

Generate as usual:

```bash
$ okapipy generate
╭──────────────────────────────────────────────────────────────────────╮
│ Wrote 679 files to ./out                                             │
╰──────────────────────────────────────────────────────────────────────╯
```

A few project-wide fields (`package`, `client_class`, `shape`,
`output`, `templates_dir`, `python_version`, …) are declared once at
the top of the manifest and apply to every spec. The per-spec fields
(`source`, `rules`, `strip_prefix`, `unmatched`, `lang`) live inside
each `specs[]` entry.

!!! note "Shape is project-wide"
    `shape: auto | models | dicts` controls the response-shape policy
    for the whole client — one project, one type surface across every
    mount.

### What gets emitted

Each spec lands in its own sub-tree under `base/<mount>/...`:

```
src/ffc/optscale/
├── client.py                          # user-layer subclass; wires both mounts
├── auth/                              # user-layer for the auth mount
│   ├── __init__.py                    # class AuthMount(AuthMountBase)
│   ├── collections/
│   └── ...
├── restapi/                           # user-layer for the restapi mount
│   ├── __init__.py                    # class RestapiMount(RestapiMountBase)
│   └── ...
└── base/                              # regenerated every run
    ├── client.py                      # OptscaleClientBase: @cached_property auth, @cached_property restapi
    ├── exceptions.py                  # vendored runtime (one set, project-wide)
    ├── filters.py, sort.py, ...
    ├── auth/                          # the auth spec's parsed tree
    │   ├── __init__.py                # class AuthMountBase
    │   ├── models.py                  # auth's Pydantic models only
    │   ├── collections/, namespaces/, ...
    └── restapi/                       # the restapi spec's parsed tree
        ├── __init__.py                # class RestapiMountBase
        ├── models.py                  # restapi's Pydantic models only
        └── ...
```

Two consequences worth knowing about:

- **Class names can't collide.** The synthetic mount-namespace class
  uses a `Mount` suffix (`AuthMountBase`, not `AuthNamespaceBase`), so
  even if a spec contains its own top-level `auth` namespace the two
  classes coexist without aliasing.
- **Models are scoped per mount.** `datamodel-code-generator` runs
  once per spec, so two services that each define a `User` schema get
  two distinct classes (`acme.optscale.base.auth.models.User` and
  `acme.optscale.base.restapi.models.User`) with no rename mangling.

### Using the client

The user-layer client class exposes one accessor per mount, plus any
root-mounted spec's own top-level (in this manifest there is no root
mount, so the client has just `auth` and `restapi`):

=== "Sync"

    ```python
    from ffc.optscale import OptscaleClient

    with OptscaleClient(base_url="https://api.example.com") as client:
        tokens = client.auth.tokens
        token = tokens.create(body={"email": "...", "password": "..."})

        orgs = client.restapi.organizations
        for org in orgs:
            print(org.id, org.name)
    ```

=== "Async"

    ```python
    from ffc.optscale import AsyncOptscaleClient

    async with AsyncOptscaleClient(base_url="https://api.example.com") as client:
        token = await client.auth.tokens.create(
            body={"email": "...", "password": "..."},
        )

        async for org in client.restapi.organizations:
            print(org.id, org.name)
    ```

Everything else — filters, sorts, pagination, `RequestOptions`,
errors, retries — works the same way it does for a single-spec
client. See [Using the client](client-usage.md) for the full surface.

## Spec-side gotchas to know about

### "All 20 paths share the prefix '/auth/v2'…"

When the spec has no `servers[]` entry and ships every path under the
same `/<service>/<version>/` prefix, okapipy fires one upfront WARNING
naming the fix:

```text
WARNING all 20 paths share the prefix '/auth/v2'; consider setting
        `strip_prefix: /auth/v2` on this spec's manifest entry to drop it
        before classification.
```

Take the suggestion. The leading segment of an un-stripped spec
usually classifies as an Action (spaCy reads `auth` as a verb), and
every downstream path then raises a "cannot be attached under
Action" warning — drowning out the actual issue.

### `[<mount>]` tags on every warning

In a multi-spec run, parser log records carry the mount name as a
bracketed prefix:

```text
WARNING [restapi] skipping path /cloud_accounts/.../bulk: action 'bulk'
        cannot be attached under Action
WARNING [auth] skipping PATCH /users/{id}: method has no canonical slot
        on collection 'User' and the operation does not fit ...
```

Use the tag to find the right rules file. Single-spec runs intentionally
drop the prefix — there's only one source, the tag would be noise.

### `unmatched:` on a per-spec basis

A spec that contains operations okapipy's routing table drops (`PUT`
on a collection, `GET` on a bare namespace, …) can keep them as flat
actions by setting `unmatched:` *inside* the spec entry:

```yaml
specs:
  - namespace: restapi
    source: ./specs/restapi.yaml
    unmatched: misc        # catch-all under client.restapi.misc.*
```

Each entry has its own `unmatched` namespace; the synthetic catch-all
nests inside the mount, not at the project root.

## Regeneration semantics in a multi-spec project

The lifecycle that single-spec projects rely on — one-shot user
stubs, regenerated `base/`, `--check` as a CI gate, drift warnings on
new children — extends per-mount in the obvious way:

- **Idempotent regeneration.** Running `okapipy generate` twice
  against an unchanged manifest writes the same bytes (except the
  state file's timestamp).
- **`okapipy generate --check`** passes when nothing changed; fails
  when any mount's `base/<mount>/...` would change, when any mount's
  user stub is missing a factory wiring, or when a mount's stale
  base files would be pruned.
- **Drift warnings localize to the right mount.** Adding a new
  top-level collection to `auth/openapi.yaml` fires a warning that
  names `src/ffc/optscale/auth/__init__.py`, not `restapi/`'s init,
  and not the project-wide `client.py`.
- **Dropping a `specs[]` entry prunes its sub-tree.** Removing the
  `auth` entry from the manifest deletes every file under
  `base/auth/` on the next run. The user-layer `auth/` tree is
  never pruned — okapipy never touches one-shot files.
- **User stubs survive every regeneration**, in every mount. Edit
  freely.

## See also

- [Quick start](quick-start.md) — the single-spec workflow and the
  full manifest schema.
- [Using the client](client-usage.md) — the API surface available on
  every collection / resource / action regardless of mount.
- [Rules and extensions](rules.md) — `x-okapipy-*` extensions and
  rules-file overrides, applied per-spec via each entry's `rules:`
  field.
- [CLI reference](../reference/cli.md) — every flag of
  `okapipy generate`.
