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
  PascalCase breadcrumb to the current segment. Collections contribute
  their singular form (`Users → User`); singletons contribute their
  singularized segment (`me → Me`, `preferences → Preference`);
  namespaces never contribute. Resource names use `"".join(breadcrumb)`
  (or `_pascal_case(parent.name)` when the breadcrumb is empty).
  Synthetic actions named off a path ending in `{id}` fall back to
  `<ParentName><Method>` so the name isn't the literal parameter token.
* **Collection-under-singleton.** `_attach` accepts `Singleton` as a
  parent for `COLLECTION` segments, so paths like `/me/orders` or
  `/orgs/current/members` (where `current` is a singleton-style
  pseudo-resource) model cleanly. A singleton is "a resource without an
  `{id}`," so what works on a resource works on a singleton.
* **Singleton-under-collection.** `_attach` accepts `Collection` as a
  parent for `SINGLETON` segments. The pattern models collection-level
  aggregate views (`/orders/stats`, `/datasets/summary`,
  `/workspaces/current/secrets/encrypted`) that aren't one of the
  items in the bag but a summary derived from them. The generated
  collection class exposes the sub-singleton as a `@property`
  alongside iteration; the sub-singleton file lives under
  `base/singletons/<name>.py` as usual.
* **Drop, don't coerce.** `_route` warns and returns when a method has no
  canonical slot for the terminal kind. The only way to keep an
  off-pattern operation is to mark it `x-okapipy-kind: action` (operation
  level), at which point `_attach_synthetic_action` synthesizes an
  `Action` under the parent.
* **Optional bulk capture of unmatched ops.** When `parse(...)` is
  called with `unmatched_namespace=<name>`, `_route` records every
  would-be-dropped `(method, path, operation)` triple in an
  `unmatched: list[_UnmatchedOp]` buffer instead of warning and
  returning. After the main walk completes the builder runs
  `_attach_unmatched_namespace(api, name, unmatched)`:
    1. Compute the snake_case form of `name` and compare against the
       snake_case form of every top-level `Namespace`, `Collection`,
       `Singleton`, and `Action` name. On match, raise
       `UnmatchedNamespaceCollisionError(name, conflict)`. The check
       runs even when the buffer is empty so a stale flag doesn't go
       unnoticed.
    2. Otherwise, synthesize a `Namespace(name=name, path="")` and
       attach one `Action` per buffered op. Each action's `name` is
       `operationId` when declared, else `<method>_<sanitized_path>`
       (the same helper synthetic actions already use). The
       contextual-PascalCase pass produces the class name from this
       attribute; namespaces don't contribute to the breadcrumb so the
       class shape is the same as a top-level action.
    3. operationId collisions across buffered ops are resolved by
       suffixing `_<n>` to the second and subsequent occurrence —
       `logging.warning(...)` so the customer can clean up the spec.

  The synthesized container's path is the empty string by design: it
  has no spec-level URL, only its child actions do. Generation downstream
  is unaffected — the synthetic namespace renders through the existing
  namespace + action templates with no special-case branches.

`_collect_spec_path_kinds` indexes path-item-level `x-okapipy-kind` hints
by cumulative path so a hint declared on `/me` propagates to `/me/refresh`
etc., resolving the intermediate `me` segment correctly. Without this an
intermediate singular noun would otherwise default to `NAMESPACE`.

`_resolve_paginated` follows precedence: per-method rules → per-method spec
extension → path-item rules → path-item spec extension → default `True`.

After every path is walked, `_apply_tag_descriptions` pulls root
`tags[]` (indexed by `_collect_tag_descriptions` into a `{name →
description}` map) and copies each matching tag's description onto the
namespace of the same name when the namespace doesn't already carry one.
Tags with blank descriptions and tags that match no namespace are
dropped silently. This is the only post-walk pass on the tree — its
job is purely to populate human-readable prose that path segments
themselves cannot supply.

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
* `cli/spec_cmd.py:parse_command` runs the full parser pipeline against a
  single positional `SOURCE`, prints a counts panel + JSON tree (or
  writes the chosen format on `--output`). `parse` is the only command
  that bypasses the manifest — it is an inspection / debugging tool.
* `cli/spec_cmd.py:generate_command` loads the project manifest
  (`./okapipy.yml` by default, overridable via `--manifest`), runs the
  parser once per `specs[]` entry, composes the trees under their
  declared mount namespaces, calls `generator.generate`, then writes
  via `vfs.write_to_disk` (with `dry_run` for `--check`). Drift
  warnings print to stderr in `Panel`s; `--quiet` suppresses them but
  pruning still runs.
* `cli/spec_cmd.py:init_command` scaffolds a starter manifest file
  (single-spec when `SOURCE` is given, empty `specs:` otherwise) and
  exits. It never touches `output_dir`.

### 1.10 Errors

`ParserError` is the base; `SpecLoadError`, `RulesFormatError`,
`NlpModelMissingError`, `InvalidStructureError`,
`UnmatchedNamespaceCollisionError` are the typed leaves. The CLI
catches `ParserError` at its boundary, prints to stderr, and exits
non-zero. `NlpModelMissingError`'s message includes the exact
`okapipy nlp fetch <lang> --cache-dir <dir>` command to fix it.
`UnmatchedNamespaceCollisionError`'s message names both the requested
namespace and the conflicting top-level node so the customer knows
which segment to rename or which name to pick.

---

## 2. Generation

### 2.1 Module map

```
src/okapipy/
├── manifest.py        GenerationManifest / SpecEntry Pydantic models;
│                      load_manifest(path) → GenerationManifest;
│                      validators for mount-namespace collisions,
│                      mandatory fields, per-spec rules paths.
└── generator/
    ├── __init__.py        Public re-exports: generate, GenerationError +
    │                      subclasses
    ├── api.py             generate(manifest, *, output_dir, check, quiet)
    │                      — orchestrator: per-spec parse loop, mount
    │                      composition, then emit
    ├── compose.py         mount_under(api, namespace) — wrap a parsed
    │                      APIModel under a synthetic mount Namespace
    │                      chain; merge_mounts(roots) — union with
    │                      cross-mount collision check
    ├── errors.py          GenerationError hierarchy
    │                      (UnknownTemplateError, TemplateRenderError,
    │                      FormatError, ManifestNotFoundError,
    │                      ManifestFormatError)
    ├── vfs.py             GeneratedFile, WriteReport, write_to_disk
    │                      (lifecycle, pruning, drift detection)
    ├── state.py           Edge / GeneratedState dataclasses + JSON
    │                      serialization for base/_generated.json
    │                      (formerly manifest.py — renamed to free the
    │                      name for the user-authored project manifest)
    ├── edges.py           compute_edges / compute_state — walks the
    │                      composed tree, mirrors the auto-wiring in
    │                      stubs.py
    ├── inline_schemas.py  Hoist anonymous schemas into components.schemas
    ├── models.py          datamodel-code-generator integration (with
    │                      the bundled relaxed templates); invoked once
    │                      per mount so each mount gets its own models.py
    ├── templating.py      Jinja2 environment factory, custom filters,
    │                      ruff isort + format post-pass
    ├── emit/
    │   ├── project.py     pyproject / README / LICENSE / gitignore /
    │   │                  python-version / py.typed
    │   ├── runtime.py     Vendor runtime/*.py verbatim into base/ once
    │   ├── client.py      Render base/client.py composing every mount
    │   ├── walk.py        emit_tree — one base file per node, mount-aware
    │   ├── stubs.py       One-shot user-layer subclass stubs (auto-wired)
    │   └── tests.py       One-shot pytest scaffolding
    ├── runtime/           Vendored library — copied verbatim into
    │                      generated packages. No Jinja, no per-API shape
    └── templates/         Default Jinja templates (project/, package/,
                           tests/, model/ for dmcg)
```

### 2.2 Pipeline

`generate(manifest, *, output_dir, check, quiet)` in `api.py` runs in
two layers: a per-spec inner loop and a single project-wide outer
pass.

```
load_manifest(path)                  GenerationManifest (Pydantic), validates
                                     mounts, package, client_class, shape
for entry in manifest.specs:         per-spec inner loop
    parse(entry.source,
          rules=entry.rules,
          lang=entry.lang or top.lang,
          strip_prefix=entry.strip_prefix,
          unmatched_namespace=entry.unmatched,
          nlp_cache_dir=top.nlp_cache_dir)        → APIModel
    compose.mount_under(api, entry.namespace)     → mount-wrapped APIModel
                                                    + raw spec for dmcg
collect mounts                        list[(mount_path, APIModel, raw_spec)]
compose.merge_mounts(mounts)          MergedTree with cross-mount collision check

emit_project_skeleton                 [one-shot] from manifest fields
emit_root_init_extension              compute import lines for top-level
                                       classes across every mount
emit_runtime                          vendor runtime/, write base/__init__.py
for each mount:
    emit_models                       dmcg → base/<mount>/models.py
                                       (skipped under shape=dicts)
    emit_tree                         base/<mount>/{namespaces,collections,
                                       resources,singletons,actions}/...
    write base/<mount>/__init__.py + sub-__init__.py markers
emit_client                           base/client.py — exposes one accessor
                                       per top-level mount; composes the
                                       cross-mount tree
emit_stubs                            one-shot user-layer subclasses across
                                       every mount
emit_tests                            one-shot tests/<mount>/...
compute_state                         base/_generated.json (always last)
```

The order matters: the project skeleton emits first so subsequent
emitters can rely on the package layout; per-mount work (`emit_models`,
`emit_tree`) runs before `emit_client` so the client can import every
mount's top-level classes; `emit_root_init_extension` precedes
`emit_runtime` so `base/__init__.py` can splice the union of mount
re-exports into a single `__all__` literal; the generated-state file is
computed last so its `base_files` reflects the full multi-mount tree.

When the manifest carries a single spec entry with `namespace: ""`, the
loop runs once and `mount_under` is a no-op — the rest of the pipeline
emits the historical flat layout.

### 2.3 Virtual filesystem (`vfs.py`)

`GeneratedFile(content, one_shot)` is a frozen dataclass. The default
`one_shot=False` covers the regenerated `base/` tree; `one_shot=True`
covers the user layer + project skeleton + tests.

`write_to_disk(vfs, output_dir, *, dry_run)` returns a `WriteReport`
(`written`, `skipped`, `pruned`, `warnings`, `would_change`). The function:

1. Reads the previous `_generated.json` from disk.
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

### 2.4 Generated-state file (`state.py` + `edges.py`)

`Edge(parent_module, factory_attr, child_user_class, child_user_module)`
records one parent → child wiring; the async sibling is implicit (`Async` +
the same names). One `Edge` per sync/async pair, not per emitted Python
class. `parent_module` and `child_user_module` carry dotted paths that
include any mount segment (`acme.commerce.users.collections.orders`),
so cross-mount edges and intra-mount edges share the same encoding —
drift detection (§3) needs no special case for "a new mount appeared."

`state.py` and `edges.py` are split on purpose: keeping the dataclasses
+ JSON encoder in `state.py` and the graph-walking logic in `edges.py`
breaks what would otherwise be an import cycle (`vfs → state → stubs →
vfs`) — `edges.py` is the only module that imports from both `stubs.py`
and `walk.py`, so it sits at a higher layer than `vfs.py` and the cycle
disappears.

The on-disk file is `base/_generated.json` (renamed from the historical
`_manifest.json`) so it is not confused with the user-authored project
manifest (`./okapipy.yml`, loaded by `okapipy/manifest.py`).

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
  PascalCase-derived. A literal `.` in the segment (e.g. `.well-known`)
  is expanded by `snake_case`/`_pascal_case` to the word `dot`/`Dot`,
  keeping the resulting identifier valid Python.
* The `__<attr>_factory__` ClassVar hook (`factory_attr(attr)`).
* A `ChildRef` dataclass per child carrying the property name, class
  name, factory hook name, the accessor's own docstring, and the
  one-line / `meta_inline` snippets the parent class's docstring map
  uses for that child.
* Docstrings via the kind-specific class builders
  (`_build_namespace_class_docstring`, `_build_collection_class_docstring`,
  `_build_resource_class_docstring`, `_build_singleton_class_docstring`)
  for class-level docstrings, and via `namespace_accessor_docstring`,
  `singleton_accessor_docstring`, `action_accessor_docstring`,
  `getitem_accessor_docstring`, and `collection_property_docstring` for
  property-level accessors.

The walker also computes the resource's path-parameter name by diffing
parent / child paths (`_new_path_param`); collections type their
`__getitem__` argument as `Any` and bind the parameter into `path_params`.

`_collect_model_names` pulls request + response model names for the imports
on collection files; `_collect_response_model_names` pulls only response
names for resource / singleton / action files (their `body` parameters are
typed `Any`, so request-model imports would lint as unused).

#### Class-docstring composition

The four kind-specific builders share a thin `_compose_class_doc_body`
helper. Each call hands it a `lead` string (sourced from the node's
`summary` / `description` — falling back to the appropriate operation
summary or a structural string per §2.12 of `REQUIREMENTS.md`) and a
sequence of `(title, items)` pairs where `items` is a `Sequence[ChildRef]`
or a `Sequence[_StaticBullet]`. Empty sections are dropped. Bullets are
rendered by `_render_bullet`, which knows how to format both kinds: a
`ChildRef` produces the `**`{attr}`** → `{ClassName}` …` head; a
`_StaticBullet` (used for the standard collection helpers `.first()` /
`.count()` / `.exists()` / `.get_page(n)`, the iteration hint, and the
CRUD entries) produces a label-only head. Both kinds carry an optional
`meta_inline` (e.g. `` `POST /admin/reindex` ``) and a `one_line`; the
renderer joins them with a period.

`build_client_class_docstring` is the only class-docstring builder that
lives at the public surface of `walk.py` — `emit/client.py` calls it
directly to compose the sync and async client class docstrings off the
top-level child lists.

#### Sync/async parity

Class docstrings name the **sync** sibling in their bullet targets — the
async tree is structurally identical, and the IDE's go-to-definition
threading from the property's own type annotation already lands on the
right async class. Property-accessor docstrings, by contrast, never name
a class explicitly: the same string is reused for the sync and async
property bodies, so pinning either prefix would mislead one of the two
readers.

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
`CommaSignedSort()`.

The template branches on the generator's `shape` parameter:

* `shape="auto"` (default) emits the dual-shape client: a `shape=`
  constructor option, a `_shape` attribute, a `shape` property,
  `with_shape("models" | "dicts")` returning a sibling via
  `object.__new__(type(self))` that shares httpx client / transport /
  strategies, and a `from_response(model_cls, raw)` helper that either
  calls `model_cls.model_validate(raw)` or returns `raw` (when
  `model_cls is None` or `_shape == "dicts"`).
* `shape="models"` drops `shape=`, `_shape`, the `shape` property, and
  `with_shape(...)`. `from_response` always validates against
  `model_cls`, falling back to `raw` only when no class was recovered
  (`model_cls is None`).
* `shape="dicts"` also drops `models.py`, every model import, and the
  `pydantic.BaseModel` / `TypeVar` machinery. `from_response(model_cls,
  raw)` ignores `model_cls` and passes `raw` through.

The walker (`emit/walk.py`) takes the same `shape` parameter and uses it
to pick body / response / iterator-item types: `auto` admits both `Foo`
and `dict[str, Any]` arms, `models` keeps only the recovered model (or
`dict[str, Any]` when none was recovered), and `dicts` types everything
as `dict[str, Any]` regardless.

`templates/package/collection.py.jinja` produces the collection class
(sync) plus its iterator class (`<C>BaseIterator`), and an `Async`-prefixed
parallel pair. The iterator constructor pulls `next_params` from
`PaginationStrategy.initial(...)`, merges in filter/sort encoded params,
overlays per-collection `RequestOptions`, and the iterator's `__next__`
loop calls `client.from_response(item_model_cls, raw)` for each row.

`get_page(page_num)` is the iterator's random-access sibling: it asks the
strategy for `page_params(page_num, page_size)` directly, threads the same
filter/sort/options accumulators, issues one request, and returns
`extract_items(...)` parsed through `client.from_response(...)`. It is
read-only on the collection — none of `filter_expr`, `sort_expr`,
`current_page_size`, or `options` is mutated — so multiple `get_page`
calls on the same collection from different threads or asyncio tasks are
safe as long as no caller is concurrently mutating those accumulators.
`first()` remains the odd one out (it temporarily writes
`current_page_size`); `get_page` deliberately reads only.

`exists()` is `count() > 0`. The collection routes both through the
strategy's `supports_count` / `count_request_params` / `extract_count`
trio; when `supports_count` is `False` they raise
`UnsupportedPaginationError`. `get_page(...)` does the same check against
`supports_random_access` — `True` for `LimitOffsetPagination` (offset =
`page_num * size`) and `PageNumberPagination` (page = `start_page +
page_num`); `False` for `CursorPagination` and `LinkHeaderPagination`,
which reject up front because the wire protocol cannot reach page N
without first consuming page N-1's continuation token.

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
* Each mount's `models.py` is post-processed to apply isort + format
  consistent with the surrounding project. Multi-mount projects run
  dmcg once per mount; per-mount `available_models` sets are passed
  into `emit_tree` so import-line filtering remains local to each
  mount.
* The vendored runtime is *not* re-templated and *not* run through ruff
  on emit — it is canonical at source. Tests in `tests/generator/` ruff-
  check the source runtime tree to catch drift early.
* `StrictUndefined` makes any missing template variable fail loudly during
  generation rather than silently emit empty strings.

### 2.13 Project-manifest loading (`okapipy/manifest.py`)

The user-authored manifest is loaded by `load_manifest(path: Path) →
GenerationManifest`. Responsibilities:

* **Format auto-detection.** `.yml` / `.yaml` go through
  `yaml.safe_load`; `.json` goes through `json.loads`. Anything else
  raises `ManifestFormatError` (a `GenerationError` subclass).
* **Pydantic v2 validation.** `GenerationManifest` and `SpecEntry`
  validate types, required fields (`package`, `client_class`, at least
  one `specs[]` entry), and that per-spec `source` is a path or
  `http(s)://` URL while `rules` is a local path only (URLs rejected,
  matching `parser.rules.load_rules`).
* **Mount-namespace collision detection.** A post-validator builds the
  fully-qualified mount paths from `specs[].namespace` (`""` →
  top-level mount, `"platform.users"` → `["platform", "users"]`) and
  raises `ManifestFormatError` when two entries resolve to the same
  fully-qualified path. The check happens before any parsing so the
  user sees a fast schema error rather than a deep stack trace from
  the emitter.
* **Relative-path resolution.** All paths in the manifest (`source`,
  `rules`, `templates_dir`, `model_templates_dir`, `nlp_cache_dir`,
  `output`) are resolved relative to the manifest file's parent
  directory, not the process cwd, so a manifest is movable with its
  consumer repo.
* **CLI override merging.** `apply_cli_overrides(manifest, output=...)`
  returns a new `GenerationManifest` with selected fields replaced —
  used by `cli/spec_cmd.py:generate_command` to honor `--output` over
  the manifest's `output` value. Other manifest fields have no CLI
  counterpart; `apply_cli_overrides` is intentionally narrow.

Errors raised here are `ManifestNotFoundError` (file missing on disk)
and `ManifestFormatError` (everything else). Both inherit from
`GenerationError` so the CLI's existing `print_error` boundary catches
them with no extra wiring.

### 2.14 PEP 621 `pyproject.toml` emission

`emit/project.py` renders the generated `pyproject.toml` from
manifest-driven fields. The PEP 621 `[project]` table is populated as
follows:

* `name` from `manifest.project_name` (defaults to the last segment of
  `package`).
* `description` from `manifest.project_description` (defaults to
  `"Generated client for <project_name>"`).
* `version` from `manifest.project_version`.
* `readme = "README.md"` — always emitted; the co-emitted README.md
  carries the same `one-shot` lifecycle.
* `requires-python` derived from `manifest.python_version`.
* `license = "<spdx-id>"` only when the value is in the recognised SPDX
  safelist (see `_SPDX_LICENSES` in `generator/api.py`). Free-form
  values like `Proprietary` are omitted because hatchling validates
  this field as an SPDX expression and would refuse the project at
  build time.
* `license-files = ["LICEN[CS]E*"]` — always emitted; the co-emitted
  LICENSE file is always present.
* `authors = [{ name = "..." }]` only when `manifest.author` is set.
* `[project.urls]` only when `manifest.repo_url` is set. The table
  carries `Homepage` and `Repository` pointing at the trimmed URL;
  `github.com` URLs also gain an `Issues = "<url>/issues"` entry.

The corresponding helper `_project_urls(repo_url)` in
`generator/api.py` returns an ordered list of `(label, url)` tuples so
the rendered TOML table is deterministic across runs.

---

## 3. Customization

The customization split is implemented entirely inside the generator. There
are no parser-side concessions.

### 3.1 Path layout

`emit/walk.py` puts every regenerated file under
`src/<package>/base/[<mount_path>/]...`, where `<mount_path>` is the
spec's manifest `namespace` field split on `.` and joined with `/`. An
empty mount (`namespace: ""`) collapses to the historical flat layout;
a dotted mount (`namespace: platform.users`) nests under intermediate
directories that may be shared by multiple specs (`platform.billing`
joins the same `platform/` parent).

`emit/stubs.py` writes the customer-facing layer at the sibling paths
(`src/<package>/[<mount_path>/]client.py` for the project-wide client,
`src/<package>/<mount_path>/collections/<c>.py` for per-spec
collections, etc.). Module names match between layers; the
`base/` segment of the import path disambiguates layer, and the
optional `<mount_path>/` segment disambiguates spec.

Class names in `base/` end with `Base` (`OrdersCollectionBase`,
`OrderResourceBase`, …); user-layer classes drop the suffix
(`OrdersCollection(OrdersCollectionBase)`). Class-name collisions
across mounts are impossible at the import level because each mount
lives in its own sub-package; the contextual-PascalCase naming engine
operates inside one mount at a time and is unchanged.

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

### 3.4 Generated-state / pruning / drift

`compute_state(api, package, base_files)` in `edges.py` walks the parser
tree symmetrically to the auto-wiring in `stubs.py` and produces one
`Edge` per child; `state.py:serialize` writes deterministic JSON into
`base/_generated.json`. The same module exposes `read_from_disk` so
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
* `okapipy spec` — `cli/spec_cmd.py`. Three commands:
  * `parse <SOURCE>` runs the parser pipeline against a single spec with
    a per-phase `rich.console.Console.status` spinner, prints a counts
    panel + syntax-highlighted JSON tree (or writes to `--output`).
    Bypasses the manifest by design — `parse` is an inspection tool.
  * `generate [--manifest PATH] [--output PATH] [--check] [--quiet]`
    loads the manifest via `okapipy.manifest.load_manifest`, applies
    `--output` (if given) via `apply_cli_overrides`, then for each
    `specs[]` entry runs `parser.parse(...)` and
    `generator.compose.mount_under(api, entry.namespace)`. The
    composed model is handed to `generator.generate(...)`, which calls
    `vfs.write_to_disk(dry_run=check)`. Drift warnings print to
    stderr in `Panel`s; `--quiet` suppresses them but pruning still
    runs. Per-phase spinners label both the parse loop ("Parsing
    `<entry.source>`") and the emit pass ("Generating client
    project"). `ManifestNotFoundError` and `ManifestFormatError`
    surface through the same `print_error` boundary used by
    `GenerationError` and `ParserError`.
  * `init [<SOURCE>] [--manifest PATH] [--package DOTTED]
    [--client-class NAME] [--force]` writes a starter
    `okapipy.yml`. Refuses to overwrite an existing file without
    `--force`. Does not invoke the parser or the generator.

`cli/console.py` owns the rich-printing helpers (`print_error`, `stderr`,
`stdout`, `is_piped`) and `setup_logging(verbose)` — `-v` enables INFO,
`-vv` enables DEBUG and prints tracebacks on error.
