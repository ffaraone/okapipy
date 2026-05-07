# Design

This document describes **how the code in `src/okapipy/` satisfies the
requirements in [`REQUIREMENTS.md`](REQUIREMENTS.md)**: module layout, data
flow, key invariants, and the decisions visible in the source.

The codebase splits cleanly into the same three subsystems as the
requirements doc:

1. **Parsing** — `src/okapipy/parser/`
2. **Generation** — `src/okapipy/generator/`
3. **Customization** — implemented entirely inside the generator
   (`emit/stubs.py`, `vfs.py`, `manifest.py`, `edges.py`)

CLI orchestration lives in `src/okapipy/cli/`; the entry point is wired
through `src/okapipy/app.py` to `okapipy = "okapipy.app:main"` in
`pyproject.toml`.

---

## 1. Parsing

### 1.1 Module map

```
src/okapipy/parser/
├── __init__.py        Public re-exports: parse, APIModel + node types,
│                      DEFAULT_NLP_CACHE_DIR, error classes
├── api.py             parse() — single public entry; orchestrates loader →
│                      rules → NLP → builder
├── loader.py          load_spec, detect_base_path, strip_base_path
├── nlp.py             spaCy JIT loader, analyze_segment, lemma_in_context,
│                      VERB_ACTION_REGISTRY
├── extension.py       readers for x-okapipy-ns / -kind / -paginated /
│                      -exclude in the spec
├── rules.py           Rules / PathRules / OperationRules Pydantic models
│                      and lookup helpers (rules-file precedence)
├── classifier.py      classify_segment + SegmentKind enum
├── builder.py         Tree assembly, naming engine, operation routing
├── model.py           Pydantic v2 nodes (mutable on purpose)
├── dump.py            JSON / YAML serialization with extension inference
└── errors.py          ParserError hierarchy
```

### 1.2 Pipeline

`parse(source, rules, lang, *, strip_prefix, nlp_cache_dir)` in
`api.py:16` wires the four phases together:

```
load_spec(source) ──► dict (refs preserved)
load_rules(rules) ──► Rules (empty when source is None)
load_pipeline(lang, cache_dir) ──► spacy.Language
build(spec, rules, nlp, strip_prefix=...) ──► APIModel
```

### 1.3 Loader (`loader.py`)

* Auto-detects path vs URL via `urllib.parse.urlparse(...).scheme`. URLs
  are fetched with `urllib.request.urlopen` (no extra deps).
* Format detection: try `json.loads`, fall back to `yaml.safe_load`. Failure
  raises `SpecLoadError`.
* `$ref`s are deliberately **not** resolved. Schema names are recovered later
  in the builder by reading the trailing segment of the `$ref` string. This
  avoids prance's recursive-resolution cost on real-world specs and keeps
  the loader free of network egress for external refs.
* `detect_base_path(spec)` reads the path component of `servers[0].url`.
  Returns `""` when no `servers` are declared.
* `strip_base_path(paths, base)` removes that prefix from each key,
  collapsing the empty result to `"/"`. Keys that do not start with `base`
  are passed through unchanged.

### 1.4 NLP layer (`nlp.py`)

Two non-obvious workarounds make small spaCy models reliable for path
segments:

* **Plural detection** — bare path tokens (`tokens`, `users`) get tagged as
  `PROPN` with `Number=Sing` by `en_core_web_sm`. Re-analyzing them inside a
  language-specific definite-article wrapper from `PLURAL_CONTEXT`
  (`"the {}"`) coaxes the tagger into a noun analysis with the right
  `Number` morphology. `_detect_plural` uses this; `lemma_in_context` reuses
  the trick to get singular lemmas.
* **Verb detection** — verbs (`reset`, `submit`) keep their `VERB` tag in
  isolation but lose it inside the article wrapper. `_analyze_token` runs
  both analyses and combines the signals.
* A small per-language **`VERB_ACTION_REGISTRY`** catches high-traffic API
  verbs (`login`, `refresh`, `revoke`, `ping`, `verify`, …) that small
  models still mistag even after the wrapper trick. Currently English only;
  other languages can fall through to spaCy alone or use
  `x-okapipy-kind: action`.

`analyze_segment(nlp, segment)` is the classifier-facing entry. It splits on
`-`/`_`, applies the **head-noun rule** (last token decides), with a
postmodifier exception: when `of` / `and` / `in` / `for` / `with` / `to` /
`by` / `from` / `on` / `at` appears between hyphenated tokens, the head is
on the *left*, so the function probes earlier tokens for plurality
(`units-of-measure`, `terms-and-conditions`).

`load_pipeline` caches `(lang, cache_dir)` pipelines process-wide
(`_PIPELINE_CACHE`). `_analyze_token` is `lru_cache`-memoized per pipeline
(maxsize 4096). On a cache miss the model is downloaded with
`subprocess.run([sys.executable, "-m", "spacy", "download", ..., "--target",
str(cache_dir)])`. `clear_pipeline_cache()` exists for tests.

### 1.5 Extensions and rules (`extension.py` / `rules.py`)

`extension.py` reads four x-okapipy-* keys from the spec. `rules.py` mirrors
the same shape using Pydantic v2 models with field aliases (`alias=
"x-okapipy-kind"` etc.) so JSON / YAML keys round-trip cleanly. Lookup
functions return values from the rules first, falling back to the spec —
the **rules-file-wins** invariant.

The `Rules` document accepts only namespace declarations and per-path /
per-method overrides; URLs are rejected (`load_rules` requires a
`Path`-resolvable source). Unknown `x-okapipy-kind` values and malformed
`x-okapipy-exclude` entries raise `RulesFormatError` with the offending
path so the customer knows where to look.

### 1.6 Classifier (`classifier.py`)

Pure function `classify_segment(*, segment, cumulative_path, parent_kind,
nlp, ns_registry, extension_hint)` returns one of five `SegmentKind` enum
members. The precedence chain is exactly the one in §1.3 of the
requirements doc; see `_classify` for the implementation.

The single non-obvious detail: a singular / unknown segment without a
namespace-registry entry resolves to `NAMESPACE` only when the parent is
the root or another namespace, and to `COLLECTION` otherwise. This matches
how real APIs use unfamiliar single-noun prefixes — the first one is
usually a folder, deeper ones are usually collections the spec didn't
pluralize.

### 1.7 Builder (`builder.py`)

`build(spec, rules, nlp, *, strip_prefix)` walks every path, classifies each
segment, calls `_attach` to find-or-create the corresponding node under the
running cursor, then `_install_operations` to route the path-item's HTTP
methods to operation slots.

Three invariants live in this module:

* **Mutate in place.** `APIModel` and its children are mutable Pydantic
  models. There are no draft / wrapper types — `cursor.namespaces.append(
  ...)` is the assembly primitive.
* **Naming.** `contextual_name(breadcrumb, current)` joins the
  PascalCase-singular-of-collection breadcrumb to the current segment.
  Resource names use `"".join(breadcrumb)` (or `_pascal_case(parent.name)`
  when the breadcrumb is empty). Synthetic actions named off a path
  ending in `{id}` fall back to `<ParentName><Method>` so the name isn't
  the literal parameter token.
* **Drop, don't coerce.** `_route` warns and returns when a method has no
  canonical slot for the terminal kind. The only way to keep an
  off-pattern operation is to mark it `x-okapipy-kind: action` (operation
  level), at which point `_attach_synthetic_action` synthesizes an
  `Action` under the parent.

`_collect_spec_path_kinds` indexes path-item-level `x-okapipy-kind` hints
by cumulative path so a hint declared on `/me` propagates to `/me/refresh`
etc., resolving the intermediate `me` segment correctly. Without this an
intermediate singular noun would otherwise default to `NAMESPACE`.

`_resolve_paginated` follows precedence: per-method rules → per-method spec
extension → path-item rules → path-item spec extension → default `True`.

`_request_info` and `_response_info` recover schema names from the raw
spec by reading the trailing segment of the `$ref` string
(`#/components/schemas/Order` → `Order`), with a fallback to inline
`title`. List-shaped responses populate `item_model` from the inner
schema, recognizing five conventional envelope keys: `items`, `data`,
`results`, `records`, `entries`.

### 1.8 Model (`model.py`)

Pydantic v2, mutable by default. Five node classes plus `Operation`. Forward
references between `Resource ↔ Singleton ↔ Collection` form a 3-way cycle,
so each class calls `model_rebuild()` at module load.

`APIModel` carries top-level `namespaces`, `collections`, `singletons`,
`actions` because real APIs commonly expose all four directly under `/`.
`Operation.response_model` is `str | None` because some 2xx responses have
no body (`204 No Content`, etc.).

### 1.9 Dump and CLI

`dump.write` infers JSON vs YAML from the path suffix; unknown suffixes raise
`ValueError` with the supported set. The CLI subcommands live in
`src/okapipy/cli/`:

* `cli/nlp_cmd.py:fetch` calls `parser.nlp.fetch_model`.
* `cli/spec_cmd.py:parse_command` runs the full pipeline, prints a counts
  panel + JSON tree (or writes the chosen format on `--output`).
* `cli/spec_cmd.py:generate_command` parses, then calls
  `generator.generate`, then writes via `vfs.write_to_disk` (with `dry_run`
  for `--check`). Drift warnings print to stderr in `Panel`s; `--quiet`
  suppresses them but pruning still runs.

### 1.10 Errors

`ParserError` is the base; `SpecLoadError`, `RulesFormatError`,
`NlpModelMissingError`, `InvalidStructureError` are the typed leaves. The
CLI catches `ParserError` at its boundary, prints to stderr, and exits
non-zero. `NlpModelMissingError`'s message includes the exact
`okapipy nlp fetch <lang> --cache-dir <dir>` command to fix it.

---

## 2. Generation

### 2.1 Module map

```
src/okapipy/generator/
├── __init__.py        Public re-exports: generate, GenerationError +
│                      subclasses
├── api.py             generate() — orchestrator, returns
│                      dict[str, GeneratedFile]
├── errors.py          GenerationError hierarchy (UnknownTemplateError,
│                      TemplateRenderError, FormatError)
├── vfs.py             GeneratedFile, WriteReport, write_to_disk
│                      (lifecycle, pruning, drift detection)
├── manifest.py        Edge / Manifest dataclasses + JSON serialization
├── edges.py           compute_edges / compute_manifest — walks the parser
│                      tree, mirrors the auto-wiring in stubs.py
├── inline_schemas.py  Hoist anonymous schemas into components.schemas
├── models.py          datamodel-code-generator integration (with the
│                      bundled relaxed templates)
├── templating.py      Jinja2 environment factory, custom filters,
│                      ruff isort + format post-pass
├── emit/
│   ├── project.py     pyproject / README / LICENSE / gitignore /
│   │                  python-version / py.typed
│   ├── runtime.py     Vendor runtime/*.py verbatim into base/
│   ├── client.py      Render base/client.py from the walker context
│   ├── walk.py        emit_tree — one base file per node
│   ├── stubs.py       One-shot user-layer subclass stubs (auto-wired)
│   └── tests.py       One-shot pytest scaffolding
├── runtime/           Vendored library — copied verbatim into generated
│                      packages. No Jinja, no per-API shape
└── templates/         Default Jinja templates (project/, package/, tests/,
                       model/ for dmcg)
```

### 2.2 Pipeline

`generate(api, raw_spec, *, output_dir, package, client_class, ...)` in
`api.py` runs the emitters in this order:

```
emit_project_skeleton           [one-shot]
emit_root_init_extension        compute import lines for top-level classes
emit_runtime                    vendor runtime/, write base/__init__.py
emit_models                     dmcg → base/models.py  (skipped if --no-models)
emit_client                     base/client.py
emit_tree                       base/{namespaces,collections,resources,
                                singletons,actions}/<...>.py
write base/<subdir>/__init__.py markers
emit_stubs                      one-shot user-layer subclasses
emit_tests                      one-shot tests/...
compute_manifest                base/_manifest.json (always last)
```

The order matters: the project skeleton emits first so subsequent emitters
can rely on the package layout; `emit_root_init_extension` runs before
`emit_runtime` so `base/__init__.py` can splice top-level `Namespace*Base`
re-exports into a single `__all__` literal; the manifest is computed last
so its `base_files` field reflects the full base tree.

### 2.3 Virtual filesystem (`vfs.py`)

`GeneratedFile(content, one_shot)` is a frozen dataclass. The default
`one_shot=False` covers the regenerated `base/` tree; `one_shot=True`
covers the user layer + project skeleton + tests.

`write_to_disk(vfs, output_dir, *, dry_run)` returns a `WriteReport`
(`written`, `skipped`, `pruned`, `warnings`, `would_change`). The function:

1. Reads the previous `_manifest.json` from disk.
2. Computes drift warnings against the *previous* on-disk state (must
   happen before writing).
3. Iterates the VFS and either writes (regenerated paths or first-run
   one-shots) or skips (existing one-shots).
4. Compares previous `base_files` to the current set; deletes any path in
   the difference.
5. Reports.

`would_change` is the OR of "any base file content changed" and "any stale
base file would be pruned", with the manifest itself excluded from the
content comparison because its `generated_at` timestamp differs every run.

### 2.4 Manifest (`manifest.py` + `edges.py`)

`Edge(parent_module, factory_attr, child_user_class, child_user_module)`
records one parent → child wiring; the async sibling is implicit (`Async` +
the same names). One `Edge` per sync/async pair, not per emitted Python
class.

`manifest.py` and `edges.py` are split on purpose: keeping the dataclasses
+ JSON encoder in `manifest.py` and the graph-walking logic in `edges.py`
breaks what would otherwise be an import cycle (`vfs → manifest → stubs →
vfs`) — `edges.py` is the only module that imports from both `stubs.py`
and `walk.py`, so it sits at a higher layer than `vfs.py` and the cycle
disappears.

### 2.5 Inline-schema flattening (`inline_schemas.py`)

dmcg emits one class per anonymous schema occurrence. A spec with
`Created.by`, `Updated.by`, `Deleted.by` all carrying the same shape would
yield `By` / `By1` / `By2` after dmcg runs. This pass:

1. Walks every schema-bearing location (`paths.*`, `components.parameters`,
   `components.requestBodies`, `components.responses`,
   `components.headers`, plus nested `properties` / `items` /
   `additionalProperties` / `allOf` / `oneOf` / `anyOf` / `not`).
2. Collects inline `type: object`-with-properties or `enum` occurrences.
3. Buckets occurrences by **structural hash** (canonical-JSON SHA-256), so
   identical shapes collapse to one component.
4. Names each bucket by priority: `title` → last property name → parent +
   last → content-hash suffix.
5. Replaces each occurrence with a `$ref`, preserving the original
   `components.schemas` set.

Result: dmcg sees a flat ref graph and emits one class per logical schema.

### 2.6 dmcg integration (`models.py`)

`emit_models(raw_spec, model_templates_dir, python_version)` materializes
the spec to a temp directory (post-flattening), invokes `dmcg.generate`
with `output_model_type=PydanticV2BaseModel`, and post-processes the
output through `ruff check --fix --select I` + `ruff format`.

The bundled `templates/model/` directory ships relaxed templates that:

* force every field optional (`| None`),
* set `extra="allow"` and `populate_by_name=True`,
* preserve dmcg's `Field(...)` call so spec constraints (`max_length`,
  `pattern`, `examples`, …) survive,
* rewrite `alias=` to `validation_alias=AliasChoices(snake, original),
  serialization_alias=original` so payloads round-trip under either name.

`public_names(source)` parses the rendered file with `ast` and returns the
set of top-level identifiers (class defs, simple assignments, annotated
assignments). The walker passes that set to every `_emit_*` function as
`available_models`; `from ..models import ...` lines are filtered against
it so the generated tree never references a symbol dmcg dropped.

### 2.7 Templating (`templating.py`)

Single Jinja2 environment per `generate()` call. Loader is
`ChoiceLoader([FileSystemLoader(templates_dir), PackageLoader("okapipy.generator", "templates")])`
when `templates_dir` is set, otherwise just the packaged loader.
Configuration is `trim_blocks=True`, `lstrip_blocks=True`,
`keep_trailing_newline=True`, `autoescape=False`, `undefined=StrictUndefined`.
Strict undefined makes a missing context variable fail loudly at render
time rather than silently emit `""`.

Custom filters: `snake_case`, `pascal_case`, `kebab_case`, `tojson`,
`py_repr`, `py_class_or_none` (renders `Order` or the literal `None`).

`render_python` is the standard Python pipeline: render → `ruff check
--fix --select I` (isort) → `ruff format`. `known_first_party` is
forwarded to ruff's isort so files that live outside the generated
package (the test scaffolding) sort the package's own imports correctly.

### 2.8 The walker (`emit/walk.py`)

`emit_tree(env, api, project_context, package_path, available_models)`
recursively emits one templated file per node. For each node it computes:

* The class name (`<ContextualPascalCase>NamespaceBase` etc.; see helpers
  `namespace_class`, `collection_class`, etc.).
* The module name (`snake_case` of the class name minus the `Base` suffix).
* The property name on parents (`snake_case(_path_segment(node.path))`) —
  drawn from the original path segment, not the class name, so
  `force-reimport` becomes `force_reimport` rather than something
  PascalCase-derived.
* The `__<attr>_factory__` ClassVar hook (`factory_attr(attr)`).
* A `_ChildRef` dataclass per child.
* Docstrings via `build_docstring` (class) and `collection_property_docstring`
  (the property accessor, sourced from the collection's `fetch` operation
  so the call-site docs describe what listing yields).

The walker also computes the resource's path-parameter name by diffing
parent / child paths (`_new_path_param`); collections type their
`__getitem__` argument as `Any` and bind the parameter into `path_params`.

`_collect_model_names` pulls request + response model names for the imports
on collection files; `_collect_response_model_names` pulls only response
names for resource / singleton / action files (their `body` parameters are
typed `Any`, so request-model imports would lint as unused).

### 2.9 Vendored runtime (`runtime/`)

Files copied verbatim into each generated package. None of them are
templated; the only per-package customization is the regenerated
`base/__init__.py` that re-exports the public names (built up in
`emit/runtime.py:_runtime_init`).

Modules and what they own:

| File | Owns |
|------|------|
| `types.py` | `UNSET` / `Unset` sentinel + `RequestOptions` dataclass. |
| `exceptions.py` | `ApiError` + 4xx/5xx/strategy/validation/configuration subclasses. |
| `filters.py` | `Filter` ABC, `AndFilter`, `OrFilter`, `NotFilter`, `Search`. Tree-walk helpers `iter_leaves(of_type=...)` and `without(of_type)`. |
| `sort.py` | `Sort` term list, composing via `+` and unary `-`; `Sort("-field")` shorthand. |
| `strategies.py` | Pagination / Filter / Sort `Protocol`s + four pagination built-ins, four filter built-ins, three sort built-ins. Filter and sort strategies return `FilterEncoding(params, raw_query)` / `SortEncoding(params, raw_query)`. |
| `transport.py` | `RetryPolicy` frozen dataclass + `RetryTransport` / `AsyncRetryTransport`. **GET-only retries** by deliberate choice (other methods are not guaranteed idempotent). Exponential backoff: `delay = backoff * 2**attempt`. |

`emit/runtime.py:emit_runtime` walks `RUNTIME_FILES` (`types`, `exceptions`,
`filters`, `sort`, `strategies`, `transport`) and writes each one with a
banner header. The package's `__init__.py` is built explicitly (not
templated) because its imports reference siblings that must always exist
in lockstep with the runtime version.

### 2.10 Generated client surface

`templates/package/client.py.jinja` produces the sync `<Client>Base` and
async `Async<Client>Base`. Construction and lifecycle match the requirements
in §2.4 of `REQUIREMENTS.md`. Strategies default to
`LimitOffsetPagination(default_page_size=100)`, `KeyValueFilter()`,
`CommaSignedSort()`. `from_response(model_cls, raw)` is a small helper that
either calls `model_cls.model_validate(raw)` (models shape) or returns
`raw` (dicts shape, or `model_cls is None`). `with_shape("dicts")` returns
a sibling instance via `object.__new__(type(self))` that shares the
underlying httpx client, transport, and strategies.

`templates/package/collection.py.jinja` produces the collection class
(sync) plus its iterator class (`<C>BaseIterator`), and an `Async`-prefixed
parallel pair. The iterator constructor pulls `next_params` from
`PaginationStrategy.initial(...)`, merges in filter/sort encoded params,
overlays per-collection `RequestOptions`, and the iterator's `__next__`
loop calls `client.from_response(item_model_cls, raw)` for each row.

`templates/package/resource.py.jinja` and `singleton.py.jinja` produce the
CRUD-method surface from the parser slot booleans (`if retrieve_op`, etc.)
and forward to a shared `_request` helper that issues the call, raises on
non-2xx, and returns `None` for 204 / empty bodies.

`templates/package/action.py.jinja` branches on operation count: a single
op renders `run(...)`; multiple ops render one method per HTTP verb.

`templates/package/namespace.py.jinja` renders folder-only nodes — no
operations, just `@cached_property` accessors for child namespaces /
singletons and `@property` for collections / actions.

### 2.11 Tests scaffolding (`emit/tests.py`)

Walks the same `APIModel` and emits one test module per node, plus a
shared `tests/conftest.py` and `tests/test_client.py`. Every emitted file
goes through `render_python` with `known_first_party=top_package` so
`from <pkg> import ...` lands in the first-party isort group consistent
with the generated project's `pyproject.toml`.

The walker accumulates an *accessor chain* — the dotted property path
from the client root (`client.commerce.orders["sample-id"].lines`) — and
sanitizes it into a Python identifier for use in test function names
(`_safe_test_attr` collapses runs of `_`).

### 2.12 Quality gates

* Every rendered Python file goes through `ruff format`. `FormatError`
  carries the offending template name and ruff stderr.
* `models.py` is post-processed to apply isort + format consistent with
  the surrounding project.
* The vendored runtime is *not* re-templated and *not* run through ruff
  on emit — it is canonical at source. Tests in `tests/generator/` ruff-
  check the source runtime tree to catch drift early.
* `StrictUndefined` makes any missing template variable fail loudly during
  generation rather than silently emit empty strings.

---

## 3. Customization

The customization split is implemented entirely inside the generator. There
are no parser-side concessions.

### 3.1 Path layout

`emit/walk.py` puts every regenerated file under `src/<package>/base/...`.
`emit/stubs.py` writes the customer-facing layer at the sibling paths
(`src/<package>/client.py`, `src/<package>/collections/<c>.py`, etc.).
Module names match between layers; the import path's `base/` segment is
what disambiguates.

Class names in `base/` end with `Base` (`OrdersCollectionBase`,
`OrderResourceBase`, …); user-layer classes drop the suffix
(`OrdersCollection(OrdersCollectionBase)`).

### 3.2 Factory hooks

For every parent → child edge in the parser tree, the walker emits a
`__<attr>_factory__: ClassVar[type[<Child>Base]] = <Child>Base` ClassVar on
the parent base class plus a property accessor that constructs the child
through that hook (`return self.__orders_factory__(client=...)`).

Why dunder-both-sides? Python does not name-mangle attributes whose names
both start *and* end with double underscores, so `self.__orders_factory__`
inside `XBase` reads exactly the same attribute the subclass override
declares (`__orders_factory__ = MyOrders`) — no `_X__orders_factory`
prefix dance. The dunder form also makes accidental shadowing by user
instance attributes implausible.

### 3.3 Auto-wiring on first generation

`emit/stubs.py:_stub_pair` renders each one-shot stub with one
`__<attr>_factory__ = UserChildClass` line per parser-tree child (sync
class assigns the sync user class, async class assigns the
`Async`-prefixed counterpart). Action stubs are leaves and carry `pass`.

Because stubs are one-shot, a *new* child added by a later spec change is
not auto-wired into the existing parent stub. That gap is what manifest +
drift detection covers.

### 3.4 Manifest / pruning / drift

`compute_manifest(api, package, base_files)` in `edges.py` walks the parser
tree symmetrically to the auto-wiring in `stubs.py` and produces one
`Edge` per child; `manifest.py:serialize` writes deterministic JSON into
`base/_manifest.json`. The same module exposes `read_from_disk` so
`vfs.write_to_disk` can compare runs.

Pruning logic in `vfs.py:write_to_disk`:

```
new_base = {p for p in vfs if "/base/" in p}
for stale in previous.base_files - new_base:
    if (output_dir / stale).exists():
        delete it (unless dry_run)
```

User-layer files are never tracked in `base_files` and are therefore never
pruned.

Drift detection in `vfs.py:_compute_drift_warnings`:

```
new_edges     = current.edges - previous.edges
removed_edges = previous.edges - current.edges
```

* For each new edge: read the parent's user-layer stub from disk; if it
  exists and does not yet contain the line `<factory_attr> =
  <child_user_class>`, emit a warning. When the parent stub does not yet
  exist, no warning fires — the stub is about to be created with the new
  auto-wiring already in place.
* For each removed edge: if the parent stub still references the now-stale
  factory hook, emit a warning suggesting the line be removed.

The warning text is rendered by `_format_new_edge_warning` /
`_format_stale_edge_warning` and carries the exact two lines (sync +
async) the customer needs to add or remove.

### 3.5 `--check` (CI gate)

`okapipy spec generate ... --check` calls `write_to_disk(..., dry_run=True)`
and exits non-zero when:

* `report.would_change` is `True` — at least one base file's content
  differs (manifest excluded for its timestamp), or one or more files
  would be pruned, **or**
* `report.warnings` is non-empty — drift warnings fired.

The CLI prints a red "--check failed" panel summarizing what was found.

### 3.6 Scope decisions visible in the code

* `customization` is a generator-only concern. The parser does not know
  about base / user layers, factory hooks, or stubs.
* The package's `__init__.py` is emitted as **empty** and one-shot
  (`emit/stubs.py:emit_stubs`), leaving curation of the public surface to
  the customer.
* `base/runtime/` is **not** customer-overridable via `templates_dir`. The
  vendored files come from `okapipy.generator.runtime` only — extension
  happens at the runtime API level (subclass `Filter` / `Sort` /
  strategies), not at the file level.
* `base/models.py` is fully regenerated on every run. There is no
  `x-okapipy-exclude-models` extension and no per-class skip mechanism;
  customers replace generated models at the call site (Pydantic
  discriminators, wrappers) when needed.
* Sync and async classes share one module file per node (one
  `<X>Base` + one `Async<X>Base`). The two are produced from the same
  Jinja template; the async surface is not a separate `aio/` subtree.

---

## 4. CLI orchestration

`src/okapipy/app.py:main` calls `cli.app()`. `src/okapipy/cli/__init__.py`
mounts two typer sub-apps:

* `okapipy nlp` — `cli/nlp_cmd.py`. The only command is `fetch <LANG>
  [--cache-dir]` which forwards to `parser.nlp.fetch_model`.
* `okapipy spec` — `cli/spec_cmd.py`. Two commands:
  * `parse` runs the parser pipeline with a per-phase
    `rich.console.Console.status` spinner, prints a counts panel +
    syntax-highlighted JSON tree (or writes to `--output`).
  * `generate` runs the parser then the generator, calls
    `vfs.write_to_disk(dry_run=check)`, prints drift warnings (unless
    `--quiet`), and reports written / skipped / pruned counts.

`cli/console.py` owns the rich-printing helpers (`print_error`, `stderr`,
`stdout`, `is_piped`) and `setup_logging(verbose)` — `-v` enables INFO,
`-vv` enables DEBUG and prints tracebacks on error.
