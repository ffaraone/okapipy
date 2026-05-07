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
| `Collection` | Plural endpoint with `fetch` (GET) / `create` (POST) and a `Resource` child | Root, `Namespace`, or `Resource` |
| `Resource` | Single item under a collection, reached via a path-parameter segment | Inside a `Collection` |
| `Singleton` | Resourceful endpoint with no enclosing collection (`/me`, `/health`) | Root, `Namespace`, `Resource`, `Singleton` |
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
  *singular* PascalCase forms of every collection encountered so far —
  namespaces and singletons do **not** contribute. Final name is
  `"".join(breadcrumb) + PascalCase(current_segment)`.
* Resource names: `"".join(breadcrumb)` (the parent collection's singular).
* Singularization runs on the head word (last hyphen-separated sub-word) via
  spaCy's lemmatizer with a definite-article wrapper (`"the X"`) to coax the
  small-model tagger into a noun analysis.
* Synthetic actions (POST on `/orders/{id}` with `x-okapipy-kind: action`)
  are named after the path's last segment unless that segment is a path
  parameter, in which case the name falls back to
  `<ParentName><Method>` (`PasswordRecoveryRequestPost`).

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

### 1.6 OpenAPI extensions

Read both from the spec and from the rules file (rules-file values win):

| Extension | Where | Effect |
|-----------|-------|--------|
| `x-okapipy-ns` (root, list of strings) | spec / rules root | Adds path-prefixes to the namespace registry. Leading `/` accepted and stripped. |
| `x-okapipy-kind: namespace\|collection\|action\|singleton\|resource` | path-item or operation | Forces the segment / operation classification. Path-item-level hints propagate to nested paths. |
| `x-okapipy-paginated: true\|false` | path-item or operation | Sets `Operation.pagination_supported`; per-method override wins, then path-item, then default `True`. |
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
) -> APIModel
```

Non-fatal warnings go to `logging`. Errors raise a `ParserError` subclass
(`SpecLoadError`, `RulesFormatError`, `NlpModelMissingError`,
`InvalidStructureError`).

### 1.10 Dump & CLI

* `okapipy.parser.dump.write(api, path)` writes the APIModel as JSON
  (`.json`) or YAML (`.yaml` / `.yml`); other extensions raise `ValueError`.
* CLI:
  * `okapipy nlp fetch <LANG> [--cache-dir PATH]` — pre-warm a spaCy model.
  * `okapipy spec parse <SOURCE> [--rules] [--lang] [--strip-prefix]
    [--nlp-cache-dir] [--output]` — parse a spec; print a counts panel +
    JSON tree (or write the chosen format). Errors print to stderr,
    exit non-zero.
* `-v` enables INFO logs; `-vv` enables DEBUG and prints tracebacks on error.

---

## 2. Generation

### 2.1 Inputs

`okapipy.generator.generate(api, raw_spec, *, ...)`:

* `api: APIModel` — output of the parser.
* `raw_spec: dict | str | Path` — original OpenAPI document; forwarded to
  `datamodel-code-generator` so the emitted models match the inputs the user
  parsed against. Accepts a dict, a path, an `http(s)` URL, or a JSON string.
* `output_dir: Path`, `package: str` (dotted, e.g. `acme.commerce`),
  `client_class: str` (PascalCase).
* `project_name: str | None`, `project_version`, `python_version` (one of
  `3.10` / `3.11` / `3.12` / `3.13`), `license` (SPDX id).
* `templates_dir: Path | None` — directory that overrides any of okapipy's
  packaged templates. Resolved before the packaged loader (ChoiceLoader).
* `model_templates_dir: Path | None` — forwarded to dmcg's
  `custom_template_dir`.
* `with_models: bool = True` — when `False`, skip emitting `base/models.py`
  entirely; operations end up untyped (raw dicts / `Any`).

### 2.2 Output

A virtual filesystem (`dict[str, GeneratedFile]`) with POSIX-style paths
relative to `output_dir`. Each `GeneratedFile` carries:

* `content: str`
* `one_shot: bool` — `True` means "write only when the path does not yet
  exist"; `False` means "rewrite every run".

### 2.3 File layout produced

```
<output_dir>/
├── pyproject.toml                                 [one-shot]
├── README.md                                      [one-shot]
├── LICENSE                                        [one-shot]
├── .gitignore                                     [one-shot]
├── .python-version                                [one-shot]
├── src/<package_path>/
│   ├── __init__.py                                [one-shot, empty]
│   ├── client.py                                  [one-shot, user-layer subclass]
│   ├── namespaces/<ns>.py                         [one-shot, user-layer subclass]
│   ├── collections/<coll>.py                      [one-shot, user-layer subclass]
│   ├── resources/<res>.py                         [one-shot, user-layer subclass]
│   ├── singletons/<sing>.py                       [one-shot, user-layer subclass]
│   ├── actions/<act>.py                           [one-shot, user-layer subclass]
│   ├── py.typed                                   [regenerated, empty marker]
│   └── base/                                      [REGENERATED — do not edit]
│       ├── __init__.py                            re-exports runtime + tree classes
│       ├── client.py                              <Client>Base + Async<Client>Base
│       ├── models.py                              dmcg-emitted Pydantic models
│       ├── _manifest.json                         pruning + drift-detection input
│       ├── exceptions.py / filters.py / sort.py / strategies.py
│       │                                          / transport.py / types.py        [vendored runtime]
│       ├── namespaces/<ns>.py                     <Ns>NamespaceBase + Async sibling
│       ├── collections/<coll>.py                  <C>CollectionBase + iterators + async
│       ├── resources/<res>.py                     <R>ResourceBase + async sibling
│       ├── singletons/<sing>.py                   <S>SingletonBase + async sibling
│       └── actions/<act>.py                       <A>ActionBase + async sibling
└── tests/                                         [one-shot scaffolding]
    ├── conftest.py
    ├── test_client.py
    ├── namespaces/test_<ns>.py
    ├── collections/test_<coll>.py
    ├── resources/test_<res>.py
    ├── singletons/test_<sing>.py
    └── actions/test_<act>.py
```

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
* `shape: "models" | "dicts"` (default `"models"`).
* `pagination_strategy`, `filter_strategy`, `sort_strategy` — defaults are
  `LimitOffsetPagination(default_page_size=100)`, `KeyValueFilter`,
  `CommaSignedSort`.

The client also exposes:

* `__enter__` / `__exit__` (sync) and `__aenter__` / `__aexit__` (async),
  plus `close()` / `aclose()`.
* `with_shape("models" | "dicts")` — return a sibling client sharing
  transport / strategies but a different shape.
* `from_response(model_cls, raw)` — deserialize one record per the
  configured shape; returns `raw` when `model_cls is None` or shape is
  `"dicts"`.

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

Every collection class exposes:

| Method | Returns | Notes |
|---|---|---|
| `filter(expr: Filter)` | `self` | Multiple calls AND-compose. |
| `order_by(term: Sort \| str)` | `self` | String shorthand wraps `Sort(...)`. |
| `page_size(n: int)` | `self` | Per-collection override. |
| `with_options(**overrides)` | `self` | Per-collection request overrides (params, headers, timeout, auth, verify, retries). |
| `all()` | `self` | Fluent identity. |
| `first()` | item or `None` | Single request, smallest page. |
| `count()` | `int` | Calls the configured `PaginationStrategy.count_request_params` + `extract_count`; raises `NotImplementedError` when the strategy's `supports_count` is `False`. |
| `__iter__` / `__aiter__` | per-collection iterator | Drives the configured `PaginationStrategy`. |
| `__getitem__(id)` | resource | Indexed accessor (no HTTP call). |
| `create(body, **overrides)` | response | Emitted only when the parser populated `Collection.create`. |

Iterators are emitted as **separate classes** in the same module
(`<Coll>BaseIterator`, `Async<Coll>BaseIterator`). Iterator state is
strategy-agnostic: an opaque `next_params: Mapping | None`, plus
`current_page` and `index`. Items are deserialized via
`client.from_response(item_model_cls, raw)`.

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
  (`total_field` accepting a dotted path, `total_header`, `content_range`).
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
  * `UnsupportedSortError` / `UnsupportedSortFieldError`

### 2.10 CLI

`okapipy spec generate <SOURCE>`:

* `--output PATH`, `--package`, `--client-class` are required.
* `--project-name`, `--project-version`, `--python-version`, `--license`,
  `--rules`, `--lang`, `--strip-prefix`, `--nlp-cache-dir`,
  `--templates-dir`, `--model-templates-dir`,
  `--no-models` (alias `--without-models`).
* `--check` — dry run. Exits non-zero when any base file would change, when
  any drift warning would fire, or when any stale base file would be pruned.
  CI gate.
* `--quiet` / `-q` — suppress drift warnings (pruning still runs).

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
| Base | `src/<package>/base/` | Generator | Rewritten every run. |
| User | `src/<package>/...` (sibling of `base/`) | Customer | Emitted once, never overwritten. |

The base layer holds machine-translated wiring; the user layer holds bare
subclass stubs that the customer is free to extend. Class names in `base/`
end with the suffix `Base`; user-layer classes drop the suffix
(`OrdersCollectionBase` → `OrdersCollection`).

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

### 3.5 Manifest

The generator writes `src/<package>/base/_manifest.json` with:

* `generator_version` — sourced from package metadata.
* `generated_at` — UTC ISO-8601 with second precision (so two runs in the
  same second produce identical manifests).
* `base_files` — the sorted set of POSIX paths the regenerated tree owns.
* `edges` — one entry per parent → child wiring in the current parser tree.
  Each edge carries `parent_module`, `factory_attr`, `child_user_class`,
  `child_user_module` (the sync user-layer class; the async sibling is
  implicit via the `Async` prefix).

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
