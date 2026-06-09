# Requirements

This document captures **what okapipy must do**.

The work is split into three subsystems that run in pipeline order:

1. **Parsing** — turn an OpenAPI 3.x document into a structural tree
   (`okapipy.parser`).
2. **Generation** — turn that tree into a runnable Python client project
   (`okapipy.generator`).
3. **Customization** — keep customer-authored code intact across regenerations
   (the two-layer split between `base/` and the user-layer stubs).

Companion document: [`DESIGN.md`](DESIGN.md) describes *how* the code meets
these requirements (modules, data flow, key invariants, decisions).

---

## 1. Parsing

### 1.1 Inputs

* **OpenAPI document** — JSON or YAML, OpenAPI 3.x, supplied as a local
  filesystem path or `http(s)://` URL. Format auto-detected from content.
* **Optional rules file** — JSON or YAML, **local path only** (URLs not
  accepted). Mirrors the OpenAPI extension shape and overrides any spec value
  on conflict.
* **Optional ISO language code** (`en`, `es`, `fr`, `de`, `it`, `pt`, `nl`)
  selecting the spaCy POS pipeline. Default `en`.
* **Optional path prefix** (`strip_prefix`) to remove from every path before
  classification. When absent, the prefix is inferred from the path component
  of the first `servers[].url`. Empty when neither applies.
* **Optional NLP cache directory.** Default `<cwd>/.spacy`. spaCy models are
  installed and reloaded from this directory; on a cache miss the model is
  downloaded via `python -m spacy download <name> --target <cache_dir>`.

### 1.2 Output

A populated `APIModel` (Pydantic v2) consisting of five node kinds:

| Node | Purpose | Where it can attach |
|------|---------|---------------------|
| `Namespace` | Folder-style grouping; no operations of its own | Root or another `Namespace` |
| `Collection` | Plural endpoint with `fetch` (GET) / `create` (POST) and a `Resource` child | Root, `Namespace`, `Resource`, or `Singleton` |
| `Resource` | Single item under a collection, reached via a path-parameter segment | Inside a `Collection` |
| `Singleton` | Resourceful endpoint with no enclosing collection (`/me`, `/health`); also collection-level aggregate views (`/orders/stats`) | Root, `Namespace`, `Collection`, `Resource`, `Singleton` |
| `Action` | Non-CRUD verb endpoint (`/login`, `/orders/{id}/submit`) | Root, `Namespace`, `Collection`, `Resource`, `Singleton` |

`Operation` is the leaf payload that records HTTP method, content types,
schema names (`request_model`, `response_model`, list `item_model`),
declared response headers, and the booleans `pagination_supported`,
`filter_supported`, `sort_supported`. Models are mutable on purpose so the
builder can attach children and operation slots in place.

### 1.3 Classification rules

For every path segment the parser applies the following precedence and stops
at the first match:

1. **Path-parameter shape** — a segment containing `{...}` is always a
   `RESOURCE_ID` and must follow a `Collection`.
2. **Explicit hint** — `x-okapipy-kind` declared on the spec path-item /
   operation, or in the rules file. **Rules-file values win on conflict.**
   `SINGLETON` is reachable *only* through this branch, because singletons
   look identical to singular-noun namespaces and NLP cannot distinguish
   them.
3. **Namespace registry** — the cumulative path appears in
   `x-okapipy-ns` (spec or rules; rules wins).
4. **NLP signal** (spaCy POS + per-language verb registry):
   * verb / verb-phrase → `ACTION`
   * plural noun → `COLLECTION`
   * singular / unknown → `NAMESPACE` if at root or under a `Namespace`,
     otherwise `COLLECTION`
5. **Fallback** — `COLLECTION` plus a `logging.warning(...)`.

### 1.4 Naming engine

* Class names use **contextual PascalCase**. The breadcrumb is a list of the
  *singular* PascalCase forms of every collection and singleton encountered
  so far — namespaces do **not** contribute (they're pure folders). Final
  name is `"".join(breadcrumb) + PascalCase(current_segment)`. Singletons
  contribute because the things they host *belong to them* (orders under
  `/me` are *Me's* orders, not generic orders); this also prevents
  file-name collisions when a top-level collection shares a segment with a
  singleton sub-collection (e.g. `/orders` next to `/me/orders`).
* Resource names: `"".join(breadcrumb)` (the parent collection's singular).
* Singularization runs on the head word (last hyphen-separated sub-word) via
  spaCy's lemmatizer with a definite-article wrapper (`"the X"`) to coax the
  small-model tagger into a noun analysis.
* Synthetic actions (POST on `/orders/{id}` with `x-okapipy-kind: action`)
  are named after the path's last segment unless that segment is a path
  parameter, in which case the name falls back to
  `<ParentName><Method>` (`PasswordRecoveryRequestPost`).
* Path segments may contain characters that are not valid in Python
  identifiers — notably a leading `.` in well-known paths
  (`/.well-known/openid-configuration`). A literal `.` is expanded to the
  word `Dot` (PascalCase) or `dot` (snake_case) so the generated class,
  module, and attribute names remain valid Python symbols. The raw
  segment is preserved on the parsed `Namespace.name` (used for HTTP
  routing); identifier sanitization happens at render time.

### 1.5 Operation routing

| Terminal kind | GET | POST | PUT | PATCH | DELETE |
|---|---|---|---|---|---|
| `Collection` | `fetch` | `create` | drop+warn | drop+warn | drop+warn |
| `Resource` | `retrieve` | drop+warn | `update` | `partial_update` | `delete` |
| `Singleton` | same as `Resource` |
| `Action` | append to `Action.operations` (any HTTP method) |
| `Namespace` | drop+warn (a bare namespace path has no operation slot) |

Operations that don't fit are **dropped, never coerced**. To keep an
otherwise-dropped operation, mark it `x-okapipy-kind: action` (operation- or
path-item-level); the builder then synthesizes an `Action` under the
collection / resource / singleton.

**Optional bulk escape hatch: `--unmatched <namespace>`.** When the
parser is invoked with a non-`None` `unmatched_namespace`, every
operation that would otherwise be dropped by the routing table above is
instead retained as a synthetic `Action` under a single top-level
`Namespace` of the supplied name. The synthetic actions carry their
original path verbatim. Each is named after its `operationId`
(snake_case for the attribute, PascalCase for the class); when no
`operationId` is declared, the name falls back to
`<method>_<sanitized_path>`, the same pattern flat-style generators
use. The flag is **CLI-only by design** — there is no rules-file key
that toggles it, because the choice is per-invocation and should not be
baked into a shared rules document.

The supplied namespace name must not collide with any existing
top-level node. Before synthesizing the container the builder compares
the snake_case form of `unmatched_namespace` against the snake_case
form of every top-level `Namespace`, `Collection`, `Singleton`, and
`Action` name. On collision the builder raises
`UnmatchedNamespaceCollisionError` naming the conflicting node, and no
tree is returned — the customer must pick a different name.

### 1.6 OpenAPI extensions

Read both from the spec and from the rules file (rules-file values win):

| Extension | Where | Effect |
|-----------|-------|--------|
| `x-okapipy-ns` (root, list of strings) | spec / rules root | Adds path-prefixes to the namespace registry. Leading `/` accepted and stripped. |
| `x-okapipy-kind: namespace\|collection\|action\|singleton\|resource` | path-item or operation | Forces the segment / operation classification. Path-item-level hints propagate to nested paths. |
| `x-okapipy-paginated: true\|false` | document root, path-item, or operation | Sets `Operation.pagination_supported`; per-method override wins, then path-item, then document root (rules > spec at each level), then default `True`. |
| `x-okapipy-exclude: "*" \| ["GET", ...]` | path-item | Drops the entire path or selected methods (case-insensitive). |

Unknown `x-okapipy-kind` values raise `RulesFormatError` at load time. Unknown
`x-okapipy-exclude` shapes are likewise rejected.

### 1.7 Filtering / skipping

The builder additionally:

* Skips an entire path when **every** non-excluded operation on that path is
  marked `deprecated: true` in the spec (no useful structural tree would
  result).
* Skips deprecated operations even when their path retains live ones.
* Honors `x-okapipy-exclude`.

### 1.8 Schema-name recovery

The spec is loaded *without* `$ref` resolution. For each operation the parser
records:

* `request_model` — last segment of `requestBody.content[*].schema.$ref`,
  falling back to inline `schema.title`.
* `response_model` — same, picked from the most specific 2xx response (200 →
  201 → any other 2xx).
* `item_model` — when the response schema is list-shaped (plain
  `type: array` or an object with one of the conventional data-array
  properties: `items`, `data`, `results`, `records`, `entries`), name the
  inner item schema; `None` otherwise.
* `response_headers` — the names declared on the chosen 2xx response (drives
  generator-side count strategies that need `Link` / `X-Total-Count`).

### 1.9 Public API

```python
okapipy.parser.parse(
    source: str | Path,
    rules: str | Path | None = None,
    lang: str = "en",
    *,
    strip_prefix: str | None = None,
    nlp_cache_dir: Path = Path.cwd() / ".spacy",
    unmatched_namespace: str | None = None,
) -> APIModel
```

The parser is **per-spec by design**: one OpenAPI document in, one
`APIModel` out. Multi-spec composition (e.g. a microservice project that
exposes several specs under one Python client) is the generator's
responsibility, driven by the project manifest defined in §2.1.

Non-fatal warnings go to `logging`. Errors raise a `ParserError` subclass
(`SpecLoadError`, `RulesFormatError`, `NlpModelMissingError`,
`InvalidStructureError`, `UnmatchedNamespaceCollisionError`).

### 1.10 OpenAPI tag descriptions → namespace prose

Namespaces are synthesized from path segments and have no spec-level
`summary` / `description` of their own. To give them readable prose the
parser reads the document's root `tags[]` array and copies
`tag.description` onto any namespace whose `name` exactly matches
`tag.name`. The merge is conservative:

* Empty / missing `description` is ignored — never overwrites with a
  blank string.
* A namespace that already carries a description (e.g. set by a future
  spec extension) is left alone.
* Tags that match no namespace synthesize nothing — they are not
  promoted into the tree.

The resulting prose flows into the generator's class-docstring builder
(see §2.13) so a hover on a namespace class shows the API author's own
words rather than a structural fallback.

### 1.11 Dump & CLI

* `okapipy.parser.dump.write(api, path)` writes the APIModel as JSON
  (`.json`) or YAML (`.yaml` / `.yml`); other extensions raise `ValueError`.
* CLI surface owned by the parser:
  * `okapipy nlp fetch <LANG> [--cache-dir PATH]` — pre-warm a spaCy model.
  * `okapipy spec parse <SOURCE> [--rules] [--lang] [--strip-prefix]
    [--nlp-cache-dir] [--unmatched NAMESPACE] [--output]` — parse a
    single spec; print a counts panel + JSON tree (or write the chosen
    format). Errors print to stderr, exit non-zero. `parse` is an
    inspection / debugging tool and stays positional even after the
    `generate` command moves to a manifest (§2.10).
* `-v` enables INFO logs; `-vv` enables DEBUG and prints tracebacks on error.

---

## 2. Generation

### 2.1 Inputs

The generator is driven entirely by a **project manifest** — a single
YAML / JSON document checked into the consumer's repo that describes
*what* client to produce and *from which* OpenAPI specs.

The Python entry point:

```python
okapipy.generator.generate(
    manifest: GenerationManifest,
    *,
    output_dir: Path,
    check: bool = False,
    quiet: bool = False,
) -> dict[str, GeneratedFile]
```

`GenerationManifest` is a Pydantic v2 model whose YAML / JSON shape is:

```yaml
# okapipy.yml — canonical project manifest
package: acme.commerce              # required; dotted Python package
client_class: CommerceClient        # required; PascalCase

project_name: acme-commerce         # optional; defaults to last segment of package
project_description: Acme SDK       # optional; PEP 621 description; defaults to
                                    #   "Generated client for <project_name>"
project_version: "0.1.0"            # optional; default "0.1.0"
python_version: "3.13"              # optional; default "3.13"
license: Proprietary                # optional; SPDX id; default "Proprietary"
author: Acme Corp                   # optional; copyright holder
repo_url: https://github.com/...    # optional; source-repository URL. Drives
                                    #   [project.urls]; github.com URLs also
                                    #   gain a synthetic Issues entry.

shape: auto                         # optional; auto | models | dicts; default auto
lang: en                            # optional; default language for every spec
nlp_cache_dir: .spacy               # optional; default <cwd>/.spacy
templates_dir: ./templates          # optional
model_templates_dir: ./model_tpls   # optional

output: ./out                       # optional; CLI --output wins on conflict

specs:                              # required; at least one entry
  - namespace: users                # required; "" mounts at the root
    source: ./specs/users.yaml      # required; path or http(s) URL
    rules: ./rules/users.yaml       # optional
    strip_prefix: /v1               # optional
    unmatched: misc                 # optional
    lang: en                        # optional; overrides top-level lang
```

Semantics:

* **One spec, root mount.** A single-entry manifest with `namespace: ""`
  generates exactly the layout an end-user gets from the today's
  single-spec invocation: the spec's tree sits at the root of the
  generated package.
* **Multiple specs, prefixed mounts.** Each spec is parsed independently
  with its own per-spec inputs (`rules`, `strip_prefix`, `unmatched`,
  `lang`), then composed under the configured `namespace`. The mount
  namespace can be dotted (`platform.users`) to nest under intermediate
  namespaces; intermediate segments are synthesized as `Namespace` nodes
  and may be shared by multiple specs (`platform.users` and
  `platform.billing` share the `platform` parent).
* **Mount-namespace collisions are errors.** Two `specs[]` entries with
  the same fully-qualified `namespace` raise `ManifestFormatError` at
  load time. Cross-spec path collisions inside the same mount are
  impossible because each spec lives in its own sub-tree.
* **No URL rules files.** `rules` accepts a local path only, matching
  the parser's invariant (§1.1).

The top-level fields drive a single generated project: one
`pyproject.toml`, one `<Client>Base`, one vendored runtime, one
`_generated.json` tracking file. The `specs[]` entries decide what lives
inside.

Field-level meaning of the shape-related options matches the existing
generator behavior:

* `shape: "auto"` (default) emits a dual-shape client. The constructor
  accepts a `shape: "models" | "dicts"` keyword, `with_shape(...)`
  returns a sibling switching shape at runtime, and bodies / returns
  admit both arms (`Foo | dict[str, Any]` / `Foo | dict[str, Any] |
  None`).
* `shape: "models"` locks the client to typed Pydantic models. The
  `shape=` constructor option and `with_shape(...)` are dropped; bodies
  are typed `Foo` and returns `Foo | None`. Per-mount `models.py` files
  are still emitted.
* `shape: "dicts"` locks the client to raw dicts. Every per-mount
  `models.py` is skipped, every model import is dropped, and bodies /
  returns are typed `dict[str, Any]` / `dict[str, Any] | None`.

The `shape` choice is project-wide — it cannot vary per spec entry,
because a single generated client has one type surface.

#### Manifest discovery and overrides

* The CLI looks for `./okapipy.yml` by default; override via
  `--manifest PATH`.
* CLI flags that map to manifest fields override the manifest on
  conflict: `--output`, `--check`, `--quiet`. No other manifest field
  has a CLI counterpart — to change `package`, `client_class`, `shape`,
  or any per-spec option, edit the manifest.
* Errors raise `ManifestNotFoundError` (file missing) or
  `ManifestFormatError` (schema violations, ambiguous mount namespaces,
  unreadable per-spec rules). Both are subclasses of `GenerationError`.

### 2.2 Output

A virtual filesystem (`dict[str, GeneratedFile]`) with POSIX-style paths
relative to `output_dir`. Each `GeneratedFile` carries:

* `content: str`
* `one_shot: bool` — `True` means "write only when the path does not yet
  exist"; `False` means "rewrite every run".

### 2.3 File layout produced

A spec's parser tree is generated under its **mount path** — the
`namespace` declared in the manifest, split on `.` and joined with
`/`. An empty mount (`namespace: ""`) puts the spec's tree at the root
of the package; a dotted mount (`namespace: platform.users`) nests
under intermediate namespaces.

Common project-level files exist exactly once per generated package:

```
<output_dir>/
├── pyproject.toml                                 [one-shot]
├── README.md                                      [one-shot]
├── LICENSE                                        [one-shot]
├── .gitignore                                     [one-shot]
├── .python-version                                [one-shot]
├── okapipy.yml                                    [user-authored — never written by the generator]
├── src/<package_path>/
│   ├── __init__.py                                [one-shot, empty]
│   ├── client.py                                  [one-shot, user-layer subclass; wires every top-level mount]
│   ├── py.typed                                   [regenerated, empty marker]
│   └── base/                                      [REGENERATED — do not edit]
│       ├── __init__.py                            re-exports runtime + every mount's top-level classes
│       ├── client.py                              <Client>Base + Async<Client>Base; composes mounts
│       ├── _generated.json                        pruning + drift-detection input (renamed from _manifest.json)
│       ├── exceptions.py / filters.py / sort.py / strategies.py
│       │                                          / transport.py / types.py        [vendored runtime; one per package]
│       └── <mount_path>/                          per-mount sub-tree (see below)
└── tests/                                         [one-shot scaffolding]
    ├── conftest.py
    ├── test_client.py
    └── <mount_path>/                              per-mount tests subtree
```

Each mount has the same internal shape regardless of how many specs the
project carries:

```
<base/ or base/<mount_path>/>
├── __init__.py                                    [regenerated, exports the mount's NamespaceBase]
├── models.py                                      [regenerated; one models.py per mount, skipped under shape=dicts]
├── namespaces/<ns>.py                             <Ns>NamespaceBase + Async sibling
├── collections/<coll>.py                          <C>CollectionBase + iterators + async
├── resources/<res>.py                             <R>ResourceBase + async sibling
├── singletons/<sing>.py                           <S>SingletonBase + async sibling
└── actions/<act>.py                               <A>ActionBase + async sibling
```

User-layer stubs mirror the same mount layout under
`src/<package_path>/<mount_path>/...` — one user-layer subclass per
generated base class, one-shot, auto-wired on first generation.

When the manifest carries a single spec with `namespace: ""` the layout
collapses to the historical flat shape (no `<mount_path>/` segment),
because the spec's tree is mounted at the root of `base/` and at the
root of the user layer. The vendored runtime, `client.py`, and
`_generated.json` always live at the package root — there is exactly
one of each per generated project, regardless of mount count.

Per-mount `models.py` exists because two specs may emit the same
generated class name (`User`, `Order`) for fundamentally different
types — isolating each spec's models inside its own mount sub-tree
avoids dmcg-level collisions without requiring a global rename.

The generated project is runnable: `uv sync && uv run ruff check . && uv run
mypy src && uv run pytest` is expected to pass immediately after generation.

### 2.4 Client construction

The generated `<Client>Base` (and `Async<Client>Base`) accept:

* `base_url: str` (required positional).
* `auth: httpx.Auth | None`.
* `timeout: httpx.Timeout | float | None | Unset` (defaults to `UNSET` →
  fall through to httpx defaults).
* `retries: RetryPolicy | None` — wraps the transport in `RetryTransport` /
  `AsyncRetryTransport` (`GET`-only retries, exponential backoff).
* `transport: httpx.BaseTransport | None` (sync) / `AsyncBaseTransport |
  None` (async) — escape hatch for tests / custom transports.
* `headers: Mapping[str, str] | None`.
* `shape: "models" | "dicts"` (default `"models"`) — **only present when the
  generator is invoked with `shape="auto"`.** Locked shapes (`shape="models"`
  or `shape="dicts"` at generation time) drop this constructor option.
* `pagination_strategy`, `filter_strategy`, `sort_strategy` — defaults are
  `LimitOffsetPagination(default_page_size=100)`, `KeyValueFilter`,
  `CommaSignedSort`.

The client also exposes:

* `__enter__` / `__exit__` (sync) and `__aenter__` / `__aexit__` (async),
  plus `close()` / `aclose()`.
* `with_shape("models" | "dicts")` — return a sibling client sharing
  transport / strategies but a different shape. **Only emitted when the
  generator was invoked with `shape="auto"`.**
* `from_response(model_cls, raw)` — deserialize one record per the
  configured shape. In auto / models mode it returns `raw` when
  `model_cls is None`; in auto mode it also returns `raw` when the runtime
  shape is `"dicts"`. In the dicts-locked mode the helper always returns
  `raw` and ignores `model_cls`.

### 2.5 Tree access

* **Namespaces** are `@cached_property` — folder nodes hold no per-call state.
* **Collections** are `@property` (fresh per access) — they carry chainable
  query state (filter / sort / page size / per-collection options).
* **Resources** are reached by indexing the parent collection
  (`collection["sample-id"]`); indexing is request-free.
* **Singletons** are `@cached_property`.
* **Actions** are `@property` (fresh per access). When the parser node has
  exactly one operation, the generator emits a single `run(...)` method;
  multiple operations produce one method per HTTP verb (`get`, `post`,
  `put`, `patch`, `delete`).

### 2.6 Collection surface

The emitted surface depends on the fetch operation's
`pagination_supported` flag (set by `x-okapipy-paginated`, see §1.6). The
**paginated** form is the default and is described first; the
**non-paginated** form is a strict subset emitted whenever the fetch
operation carries `pagination_supported=False`.

#### 2.6.1 Paginated collection (default)

Every paginated collection class exposes:

| Method | Returns | Notes |
|---|---|---|
| `filter(expr: Filter)` | `self` | Multiple calls AND-compose. |
| `order_by(term: Sort \| str)` | `self` | String shorthand wraps `Sort(...)`. |
| `page_size(n: int)` | `self` | Per-collection override. |
| `with_options(**overrides)` | `self` | Per-collection request overrides (params, headers, timeout, auth, verify, retries). |
| `all()` | `self` | Fluent identity. |
| `first()` | item or `None` | Single request, smallest page. |
| `count()` | `int` | Calls the configured `PaginationStrategy.count_request_params` + `extract_count`; raises `UnsupportedPaginationError` when the strategy's `supports_count` is `False`. |
| `exists()` | `bool` | Equivalent to `count() > 0`; inherits the same `UnsupportedPaginationError` constraint. |
| `get_page(page_num: int)` | `list[item]` | 0-indexed direct page fetch via `PaginationStrategy.page_params`; raises `UnsupportedPaginationError` when the strategy's `supports_random_access` is `False` (cursor and link-header strategies are inherently sequential). Designed for parallel page fetches. |
| `__iter__` / `__aiter__` | per-collection iterator | Drives the configured `PaginationStrategy`. |
| `__getitem__(id)` | resource | Indexed accessor (no HTTP call). |
| `create(body, **overrides)` | response | Emitted only when the parser populated `Collection.create`. |

Iterators are emitted as **separate classes** in the same module
(`<Coll>BaseIterator`, `Async<Coll>BaseIterator`). Iterator state is
strategy-agnostic: an opaque `next_params: Mapping | None`, plus
`current_page` and `index`. Items are deserialized via
`client.from_response(item_model_cls, raw)`.

#### 2.6.2 Non-paginated collection

When the fetch operation has `pagination_supported=False`, the
collection class is emitted with a strictly smaller surface — the
endpoint returns the whole result set in one response, so methods that
exist only to talk to a pagination strategy are dropped entirely:

* **Dropped:** `page_size(n)`, `get_page(n)`, the `<Coll>Iterator` /
  `Async<Coll>Iterator` classes, the `current_page_size` attribute on
  the collection, and the import of `UnsupportedPaginationError`. The
  collection never references `client.pagination_strategy`.
* **Kept, but simplified:**
  * `first()` issues a single GET and returns the head item or `None` —
    there is no `page_size=1` hint to force, because the server returns
    everything regardless.
  * `count()` issues a single GET and returns `len(items)` from the
    envelope. It is always supported (no `UnsupportedPaginationError`
    branch).
  * `exists()` issues a single GET and returns `bool(items)`.
  * `__iter__` / `__aiter__` issue a single GET and yield every item
    from the envelope as an (async) generator — no iterator class, no
    state machine.

Envelope parsing in the non-paginated path reuses the same item
extractor as the pagination strategies — `extract_envelope_items` from
the runtime — so the recognised shapes (top-level array; or an object
with an `items` / `data` / `results` key) match the paginated path. The
helper is a public symbol on the vendored runtime (`from
<pkg>.base.strategies import extract_envelope_items`).

`filter()`, `order_by()`, `with_options()`, `__getitem__`, and
`create()` are emitted unchanged — they are orthogonal to pagination.

### 2.7 Resource / singleton surface

Each emits the methods the parser populated, one per CRUD slot:

| Slot | Generated method | Body |
|---|---|---|
| `retrieve` | `retrieve(**overrides)` | none |
| `update` | `update(body, **overrides)` | required |
| `partial_update` | `patch(body, **overrides)` | required |
| `delete` | `delete(**overrides)` | spec-driven |

The `body` argument is typed as `Any`. Sub-collections, sub-singletons, and
actions hang off resource and singleton classes through the same factory-hook
mechanism as namespaces.

### 2.8 Strategies

The vendored runtime ships:

* **Pagination:** `LimitOffsetPagination`, `PageNumberPagination`,
  `CursorPagination`, `LinkHeaderPagination`. Each requires
  `default_page_size` (the wire dialect carries it; `None` would leave the
  client unable to predict what size is being sent). Each declares a
  `supports_count` capability driven by configurable count sources
  (`total_field` accepting a dotted path, `total_header`, `content_range`)
  and a `supports_random_access` capability — `True` for offset and
  page-number paginations (which can compute params for any page directly),
  `False` for cursor and link-header paginations (which can only walk
  pages sequentially).
* **Filter:** `Filter` ABC + `AndFilter` / `OrFilter` / `NotFilter` /
  `Search`. Concrete strategies: `KeyValueFilter` (conjunctive equality),
  `KeyOpValueFilter` (Django-style suffixes), `SearchFilterStrategy` (single
  `Search` leaf), `JsonFilterStrategy` (whole tree as one JSON param).
  Strategies return a `FilterEncoding(params, raw_query)` so RQL-style
  expressions (`?and(eq(f,v),...)`) can be appended verbatim.
* **Sort:** `Sort` ABC composing via `+` and unary `-`. Concrete strategies:
  `CommaSignedSort`, `KeyDirectionSort`, `JsonApiSort`. Returns
  `SortEncoding(params, raw_query)` — same dual surface as filters.

Strategies are **client-wide**: configured at construction, applied to every
collection. There is no per-collection override.

### 2.9 Errors

Generated client maps every non-2xx httpx response to an `ApiError`
subclass. The hierarchy:

* `ApiError` (base; carries `status_code`, `request`, `response`)
  * `ClientError` (4xx)
  * `ServerError` (5xx)
  * `ResponseValidationError` (2xx body failed Pydantic validation)
  * `ConfigurationError` (invalid strategy / option)
  * `UnsupportedFilterError` / `UnsupportedFilterKeyError`
  * `UnsupportedPaginationError` (strategy can't satisfy `count()` or
    `get_page()` because the wire protocol fundamentally doesn't allow it)
  * `UnsupportedSortError` / `UnsupportedSortFieldError`

### 2.10 CLI

The generator exposes two commands. Both are manifest-driven; all
project-level and per-spec configuration lives in the manifest, not in
flags.

#### `okapipy spec generate`

```
okapipy spec generate [--manifest PATH] [--output PATH] [--check] [--quiet]
```

* `--manifest PATH` — manifest file to read. Default `./okapipy.yml`.
  Missing file raises `ManifestNotFoundError`.
* `--output PATH` — output directory. Overrides the manifest's `output`
  on conflict. Required if the manifest omits `output`.
* `--check` — dry run. Exits non-zero when any base file would change,
  when any drift warning would fire, or when any stale base file would
  be pruned. CI gate.
* `--quiet` / `-q` — suppress drift warnings (pruning still runs).

All other settings — `package`, `client_class`, `shape`, per-spec
sources, rules, `strip_prefix`, `unmatched`, language, templates —
are read from the manifest. There is no positional `SOURCE`; multi-spec
projects use multiple `specs[]` entries, single-spec projects use one.

The `--unmatched` flag exists per spec entry in the manifest, not as a
CLI flag. Collision with an existing top-level node inside that spec's
mount aborts generation with `UnmatchedNamespaceCollisionError`,
naming the spec source so the user knows which entry to fix.

#### `okapipy spec init`

```
okapipy spec init [<SOURCE>] [--manifest PATH] [--package DOTTED] [--client-class NAME] [--force]
```

Scaffolds a starter `okapipy.yml` so a new project doesn't need to be
typed by hand. Behavior:

* Writes the manifest to `--manifest PATH` (default `./okapipy.yml`).
  Refuses to overwrite an existing file unless `--force` is set.
* When `SOURCE` is given, populates one `specs[]` entry with that
  `source` and `namespace: ""` (single-spec, root mount).
* When `SOURCE` is omitted, writes a manifest with an empty `specs:
  []` and inline comments demonstrating the multi-spec shape — the
  user fills the entries in.
* `--package` and `--client-class`, when set, are written to the
  manifest verbatim. When either is omitted, the field is written as
  a TODO placeholder so the file does not validate until the user
  edits it (`init` produces a *starter*, never a runnable manifest by
  accident).
* Does not invoke the parser or generator; it is purely a scaffolder
  for the manifest file.

### 2.11 Quality gates

* Generated Python is post-processed with `ruff check --fix --select I`
  (isort) followed by `ruff format`. A failure raises `FormatError`
  carrying the offending template name and ruff stderr — never silently
  emits unformatted code.
* `models.py` is delegated to `datamodel-code-generator`. Inline schemas
  are first hoisted into `components.schemas` (deduplicated by structural
  hash) so dmcg does not produce `By` / `By1` / `By2` chains for repeated
  anonymous shapes.
* The vendored runtime is *not* re-templated and *not* run through ruff —
  it is already canonical at source.
* The walker filters every `from ..models import ...` line against the set
  of identifiers `models.py` actually emits, so the generated tree never
  references a symbol dmcg dropped (primitive aliases, empty objects, etc.).

### 2.12 Generated client docstrings

Every class in the regenerated `base/` tree carries an IDE-friendly
docstring composed of two parts: a **lead paragraph** sourced from the
parser node's `summary` / `description` (or a structural fallback when
neither is set), followed by a **markdown map** of the immediately
reachable children. Maps use only the markdown subset every popular IDE
renders inside hover tooltips — ATX headings (`#### …`), bullet lists
(`-`), inline `code`, and **bold**. No tables, no HTML, no fenced links.

The required sections per node kind:

| Class | Sections (omitted when empty) |
|---|---|
| `<Client>Base` / `Async<Client>Base` | `Top-level collections`, `Top-level singletons`, `Top-level namespaces`, `Top-level actions`. |
| `<Ns>NamespaceBase` | `Sub-namespaces`, `Collections`, `Singletons`, `Actions`. |
| `<Coll>CollectionBase` | `Item access`, `Operations on the collection`, `Actions`. The first is omitted when the collection has no resource child. |
| `<Res>ResourceBase` | `Operations` (CRUD verbs the spec actually declared), `Sub-collections`, `Sub-singletons`, `Actions`. |
| `<Sing>SingletonBase` | Same shape as Resource minus `[id]` access. |
| `<Action>ActionBase` | Single-op: the operation's own summary/description. Multi-op: `#### Operations` listing every HTTP verb with its method and path. |

Bullet entries follow the same template:

```
- **`{attr}`** → `{ClassName}` — {optional `METHOD path` meta}. {one-line summary}.
```

Where `{ClassName}` always names the **sync** sibling (the async sibling
is reachable via the property's own type annotation) and the optional
`METHOD path` meta appears for action children and for the `.create(body)`
/ CRUD entries on collections / resources / singletons. The static
`Operations on the collection` section additionally lists `.first()`,
`.count()`, `.exists()`, `.get_page(n)`, and the `for item in collection: ...`
iteration hint regardless of spec content — those are surface guarantees
of the runtime.

Property accessors (every `@property` / `@cached_property` and the
`__getitem__` of a collection) carry a separate, shorter docstring — a
one-liner intended for the call-site hover. Accessor docstrings are
sync/async-agnostic: they never name a class explicitly, because the
same string is reused for both siblings and pinning either prefix would
mislead the other reader. The actual return type comes from the
property's own annotation.

Sources for the lead paragraph and one-liner, in priority order:

* the node's own `summary` and `description`;
* for collections, the fetch operation's `summary` / `description`;
* for singletons, the retrieve operation's `summary` / `description`;
* for single-op actions, the only operation's `summary` / `description`;
* a structural fallback (e.g. `` "Collection at `/orders`." ``).

---

## 3. Customization

### 3.1 Goal

Re-running the generator after a spec change must:

1. Bring the `base/` tree fully in line with the new spec.
2. Leave every customer-authored file strictly alone.
3. Report exactly what manual edit (if any) is required when spec growth
   adds a new node the customer would normally subclass.

### 3.2 Two layers

| Layer | Path | Owner | Lifecycle |
|-------|------|-------|-----------|
| Base | `src/<package>/base/[<mount_path>/]...` | Generator | Rewritten every run. |
| User | `src/<package>/[<mount_path>/]...` (sibling of `base/`) | Customer | Emitted once, never overwritten. |

The base layer holds machine-translated wiring; the user layer holds bare
subclass stubs that the customer is free to extend. Class names in `base/`
end with the suffix `Base`; user-layer classes drop the suffix
(`OrdersCollectionBase` → `OrdersCollection`). The optional
`<mount_path>/` segment is present once per spec entry whose
`namespace` is non-empty (§2.3); a single root-mounted spec collapses
to the flat layout.

### 3.3 Wiring lives in the base layer

Every parent → child edge in the parser tree corresponds to one
`__<attr>_factory__: ClassVar[type[<Child>Base]]` attribute on the parent
base class plus a property accessor that constructs the child via that
factory. `__<attr>_factory__` is dunder-both-sides on purpose: Python does
not name-mangle that form, so a subclass override
(`__orders_factory__ = MyOrders`) is read verbatim from inside the parent's
`@property` body.

### 3.4 Auto-wiring on first generation

Each one-shot user-layer stub is generated **already wired**: every
`__<child>_factory__` is assigned to the user-layer subclass for that child
(sync stub gets the sync class, async stub gets the `Async`-prefixed
counterpart). The customer's tree is on the wire from the first interpreter
run with no edits required.

The trade-off: stubs are one-shot, so a *new* child added by a later spec
change is not re-wired automatically. That is the case the manifest +
drift detection exists to handle.

### 3.5 Generated-state file

The generator writes `src/<package>/base/_generated.json` (formerly
`_manifest.json`; renamed to disambiguate from the user-authored
project manifest in §2.1) with:

* `generator_version` — sourced from package metadata.
* `generated_at` — UTC ISO-8601 with second precision (so two runs in the
  same second produce identical generated-state files).
* `base_files` — the sorted set of POSIX paths the regenerated tree owns
  **across every mount**. Pruning operates on the union; a spec removed
  from the manifest in a later run will see its entire sub-tree
  disappear.
* `edges` — one entry per parent → child wiring in the composed tree.
  Each edge carries `parent_module`, `factory_attr`, `child_user_class`,
  `child_user_module` (the sync user-layer class; the async sibling is
  implicit via the `Async` prefix). Mount-namespace parent → child
  edges (the synthetic mount node into the spec's top-level tree) are
  recorded the same way as any other parent → child edge, so drift
  detection flags a new spec the same way it flags a new namespace
  inside a spec.

### 3.6 Pruning

On the next run, `write_to_disk` reads the previous manifest before writing
and computes `previous.base_files - current.base_files`. Any path in that
difference that still exists on disk is **deleted** — those files belong
to nodes the spec no longer declares. User-layer files are never tracked
in the manifest and are never pruned.

### 3.7 Drift detection

`current.edges - previous.edges` is the set of new wirings; `previous.edges -
current.edges` is the set of removed wirings. For each:

* **New edge** — if the user-layer parent stub already exists on disk
  (i.e. is one-shot and was emitted in a previous run) and does not yet
  contain the line `__<factory_attr>__ = <child_user_class>`, emit a
  warning naming the file and the exact two lines (sync + async) to add.
  When the parent stub does not yet exist, no warning fires — the stub is
  about to be created with the new auto-wiring already in place.
* **Removed edge** — if the parent stub still references the now-stale
  `__<factory_attr>__`, emit a warning suggesting the line be removed and
  the orphaned user-layer module deleted.

### 3.8 CI gate (`--check`)

Computes the same `WriteReport` without touching disk. Exits non-zero when:

* any base file would be written with different content (manifest excluded
  because of its timestamp), **or**
* any one-shot file is missing, **or**
* any drift warning would fire, **or**
* any stale base file would be pruned.

### 3.9 What customization is **not**

* **No protected-region markers** in regenerated files.
* **No template overrides for the vendored runtime.** Customers extend the
  runtime by subclassing `Filter` / `Sort` / strategies in their own code,
  not by replacing the vendored files.
* **No model exclusion.** `base/models.py` is fully regenerated; if a
  generated model doesn't fit, customers replace it at the call site
  (Pydantic discriminators, wrappers) rather than editing `models.py`.
* **No re-export curation in `<package>/__init__.py`.** It is emitted as an
  empty one-shot file; the customer chooses what (if anything) to surface
  from there.
* **No backwards compatibility with hand-written code from before the
  base/user split** — customers regenerate.
