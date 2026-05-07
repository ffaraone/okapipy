# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

okapipy turns an OpenAPI 3.x document into a typed Python client. It runs in
two stages:

1. **Parser** (`src/okapipy/parser/`) — lifts the flat list of paths into a
   hierarchical structural tree of `Namespace → Collection → Resource →
   (Sub-Collection | Sub-Singleton | Action)`, with `Singleton` and `Action`
   also legal at the root or directly under a namespace.
2. **Generator** (`src/okapipy/generator/`) — walks the parsed `APIModel`
   and emits a runnable Python project: a regenerated `base/` layer
   (HTTP transport, Pydantic models, vendored runtime, tree wiring) and a
   one-shot user layer of subclass stubs the customer is free to edit.
   Sync and async clients are siblings, both produced from the same
   templates.

The two-layer split is what makes regeneration lossless: re-running the
generator after a spec change rewrites `base/` and leaves the user layer
strictly alone.

## Where the design lives

Two documents in [`design/`](design/) cover the contract a maintainer must
honor. Both are organized into the same three sections — parsing,
generation, customization; treat them as authoritative:

* [`design/REQUIREMENTS.md`](design/REQUIREMENTS.md) — what the code must do.
* [`design/DESIGN.md`](design/DESIGN.md) — how the code does it (modules,
  data flow, key invariants).

Read the relevant section of both before changing parser, generator, or
customization behavior. When you change behavior, update both files —
requirements drift first, design drift follows soon after.

The user-facing documentation site (mkdocs, rendered to
<https://ffaraone.github.io/okapipy/>) lives in [`docs/`](docs/) and is a
separate concern: `design/` describes the contract a maintainer must honor;
`docs/` describes the experience an end user gets.

## Common commands

Dependencies are managed with `uv`. Python is pinned to 3.13 via
`.python-version` because spaCy lacks 3.14 wheels. The first NLP-dependent
run downloads `en_core_web_sm` (~12 MB) into `./.spacy/`; subsequent runs
are offline.

```bash
uv sync                                  # install deps (incl. dev group)
uv run pytest                            # full suite + coverage report
uv run pytest tests/parser/test_X.py     # single file
uv run pytest -k "name_of_test"          # single test by substring
uv run pytest --no-cov                   # faster: skip coverage
uv run mypy src/okapipy/parser           # strict type-check (gated on parser only)
uv run ruff check src tests              # lint
uv run mkdocs serve                      # preview the docs site locally
uv run okapipy nlp fetch en              # pre-download spaCy model into ./.spacy
uv run okapipy spec parse <SOURCE>       # parse a spec; SOURCE is a path or http(s) URL
uv run okapipy spec generate <SOURCE> --output ./out --package acme.commerce --client-class CommerceClient
```

`pyproject.toml` configures `--cov=okapipy`, so plain `uv run pytest` always
emits coverage; `htmlcov/` and `coverage.xml` are written. `[tool.mypy]`
runs strict mode **only** for `okapipy.parser.*` — keep new parser code
passing strict.

## Parser pipeline

The parser is a linear pipeline; one phase per module under
`src/okapipy/parser/`. Section 1 of [`design/REQUIREMENTS.md`](design/REQUIREMENTS.md)
covers the contract; section 1 of [`design/DESIGN.md`](design/DESIGN.md) covers
the implementation. Quick map:

| Module | Role |
| --- | --- |
| `loader.py` | Load a spec from a path or URL (JSON/YAML auto-detected). `$ref`s are kept intact so the builder can recover schema names from the trailing segment of the ref string. |
| `nlp.py` | spaCy-backed POS/morphology, with a `"the X"` wrapper trick for plural detection and a bare-form pass for verb detection. JIT model download into `cache_dir` (default `./.spacy`). |
| `extension.py` | Read `x-okapipy-ns`, `x-okapipy-kind`, `x-okapipy-exclude`, `x-okapipy-paginated` from the spec. |
| `rules.py` | Project-local rules file mirroring the same shape. **Rules-file values win over the spec on every conflict.** Local file only — no URL support. |
| `classifier.py` | Decide if a single segment is `NAMESPACE / COLLECTION / RESOURCE_ID / SINGLETON / ACTION`. Precedence: path-param → `x-okapipy-kind` → namespace registry → spaCy → fallback. |
| `builder.py` | Walk paths and **mutate Pydantic models in place** (no draft/wrapper types). Owns the naming engine and operation routing. |
| `model.py` | Pydantic v2 node models (mutable on purpose). |
| `dump.py` | `write(api, path)` — JSON/YAML inferred from extension; unknown extension → `ValueError`. |
| `api.py` | `parse(source, rules=None, lang="en", *, strip_prefix=None, nlp_cache_dir=cwd/.spacy)` — single public entry. Returns `APIModel`; non-fatal warnings go to `logging`. |

Key invariants worth remembering when editing:

* `contextual_name(breadcrumb, current)` joins the **full** breadcrumb of
  singular collection names — namespaces and singletons do **not** enter
  the breadcrumb.
* Operation routing is fixed: `Collection` accepts `GET → fetch` /
  `POST → create`; `Resource` and `Singleton` accept the standard CRUD
  verbs; `Action` collects any verb on `Action.operations`.
* Operations that don't fit (PUT on a bare collection, POST on a Resource
  without an explicit `x-okapipy-kind: action` hint) are **dropped with a
  warning**, never coerced into a synthetic action.
* `x-okapipy-exclude: "*"` skips a whole path; `x-okapipy-exclude: [DELETE, ...]`
  (case-insensitive) skips just those methods.

## Generator pipeline

The generator's entry point is `generate(api, raw_spec, *, output_dir,
package, client_class, ...)` in `src/okapipy/generator/api.py`. It returns
a virtual filesystem (`dict[str, GeneratedFile]`) that the CLI flushes via
`vfs.write_to_disk`; tests inspect the dict directly. Section 2 of
[`design/REQUIREMENTS.md`](design/REQUIREMENTS.md) covers the contract;
section 2 of [`design/DESIGN.md`](design/DESIGN.md) covers the implementation.

Each `GeneratedFile` carries a `one_shot` flag:

* `one_shot=False` — files under `src/{package}/base/`. Rewritten on every
  run.
* `one_shot=True` — user-layer subclass stubs, project skeleton
  (`pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`,
  `.python-version`), and the generated test scaffolding. Written only
  when the target path does not yet exist.

Module map (under `src/okapipy/generator/`):

| Module | Role |
| --- | --- |
| `api.py` | Orchestrates every emitter; returns the merged virtual FS. |
| `vfs.py` | `GeneratedFile` dataclass + `write_to_disk(vfs, output_dir, dry_run=...)` honoring lifecycle flags. |
| `templating.py` | Jinja2 environment factory (`StrictUndefined`, `ChoiceLoader` for user template overrides) + custom filters (`snake_case`, `pascal_case`, `py_type`, …). |
| `models.py` | Wraps `datamodel-code-generator` for `base/models.py`. |
| `manifest.py` / `edges.py` | `_manifest.json` writer + parser-tree edge diff for drift detection. |
| `inline_schemas.py` | Inline anonymous request/response schemas before handing the spec to dmcg. |
| `emit/project.py` | Project skeleton (`pyproject.toml`, README, LICENSE, ...). |
| `emit/runtime.py` | Vendor `generator/runtime/` verbatim into `base/runtime/`. |
| `emit/client.py` | `client.py` (sync `<Client>Base` + `Async<Client>Base`). |
| `emit/walk.py` | Tree walk → one base file per node. Owns the docstring-from-spec helpers (`build_docstring`, `collection_property_docstring`). |
| `emit/stubs.py` | One-shot user-layer subclass stubs, auto-wired with `__<child>_factory__ = <UserChild>` lines. |
| `emit/tests.py` | Generated test scaffolding (one file per node, sync + async cases, `pytest-httpx` mocks, `shape="dicts"` so tests don't depend on dmcg's output). |
| `runtime/` | Vendored library (`Filter`, `Sort`, strategies, `RetryPolicy`, `Transport`, `RequestOptions`, `UNSET`). Copied verbatim into every generated package — no Jinja, no per-API shape. Generated clients depend on `httpx` and `pydantic` directly, **not** on okapipy. |
| `templates/` | Default Jinja templates. User overrides via `templates_dir`. |

### Generator invariants worth remembering

* The runtime library is **vendored** into each generated package. Generated
  clients have no runtime dep on okapipy.
* `models.py` is generated by `datamodel-code-generator`; we do not write
  our own model emitter. Users override via `model_templates_dir`.
* All wiring (parent → child properties, `__call__`/`__getitem__`) lives on
  `*Base` classes. User stubs specialize via `__<child>_factory__`
  ClassVars; never re-declare wiring in the user layer.
* `__<child>_factory__` is dunder-both-sides on purpose — Python does not
  name-mangle that form, so the override on a user subclass is referenced
  verbatim.
* Iterators are emitted as their own class (`<Coll>BaseIterator` /
  `Async<Coll>BaseIterator`) in the same module as their collection. State
  is strategy-agnostic: an opaque `next_params: Mapping | None` plus
  `current_page` and `index`.
* `count()` is emitted on a collection only when the configured
  `PaginationStrategy.supports_count` is `True`.
* Generated Python is post-formatted via `ruff format` (subprocess); a
  failure raises `GenerationError`.

## CLI

`pyproject.toml` wires `okapipy = "okapipy.app:main"` →
`src/okapipy/cli/__init__.py` (typer). Two sub-apps:

* `okapipy nlp fetch <LANG> [--cache-dir]` — pre-warm the spaCy model.
* `okapipy spec parse <SOURCE> [--rules] [--lang] [--strip-prefix] [--nlp-cache-dir] [--output]`
  — parse and print a counts panel; with `--output`, dumps the tree
  (format inferred from `.json/.yaml/.yml`).
* `okapipy spec generate <SOURCE> --output --package --client-class
  [--rules] [--lang] [--strip-prefix] [--nlp-cache-dir] [--templates-dir]
  [--model-templates-dir] [--no-models | --without-models] [--check]
  [--quiet]` — generate a full client project. `--check` is the CI gate:
  exits non-zero if any base file would change, any drift warning fires,
  or any stale base file would be pruned.

`SOURCE` accepts file paths and `http(s)` URLs. `ParserError` and
`GenerationError` are caught at the CLI boundary, printed to stderr, and
the process exits non-zero.

## Coding best practices

These are the rules in force across the repo, derived from the code and
prior feedback rounds.

### Style

* `ruff` with `max-line-length = 100`; isort `known-first-party = ["okapipy"]`.
* `from __future__ import annotations` everywhere.
* Type hints are mandatory in parser code (`mypy --strict` gates it). The
  generator package is moving to strict; new modules should pass strict
  from day one.
* **No underscore-prefixed "private" functions.** Module-internal helpers
  (only used inside the same source file) are allowed; otherwise expose
  them without the underscore.
* **No import aliases** unless strictly necessary.
* **All imports at the top of the module.** Inline `from x import y`
  inside a function is reserved for breaking real circular imports — and
  the parser/generator dependency graph is acyclic, so you should not
  need them.
* **No module-level helper functions in *generated* code.** Helpers go on
  the owning class as `@staticmethod`. The okapipy source itself follows
  the same rule for consistency unless a helper is genuinely shared across
  classes.
* Generated files are post-formatted with `ruff format`; templates only
  need to be syntactically correct.

### Behavior

* **Parser mutates Pydantic models in place.** No draft dataclasses, no
  wrapper types, no separate immutability pass. Pydantic v2 `BaseModel`
  is mutable by default — use it.
* **Don't coerce, drop with a warning.** Operations that don't fit a
  terminal kind (POST on a Resource without an action hint, PUT on a
  Collection, etc.) are dropped with a `logging.warning(...)`. Synthetic
  actions exist only when the user opts in via `x-okapipy-kind: action`.
* **Rules-file values win over spec values** on every conflict. If you
  add a new extension, add the same alias to `Rules`/`PathRules`/
  `OperationRules` and resolve precedence in the same order in
  `builder.py`.
* **Errors are typed.** `ParserError` and `GenerationError` are the two
  hierarchies that bubble up to the CLI. Do not raise bare `Exception`
  out of the public API.
* **Don't change CLI flags or `Operation` model fields without updating
  the design docs.** Both are part of an external contract — generated
  projects depend on the model shape, CI pipelines depend on the flags.

## Documentation best practices

okapipy maintains three different kinds of writing. They have different
audiences and different rules.

### 1. Code docstrings

* Every public function, class, and module gets a docstring. Google style
  for parser code; one-paragraph summary for everything else is fine.
* **Docstrings are self-contained.** Do not write "see file X" or
  "described in §Y of doc Z". A reader landing on the symbol from an
  IDE should understand it without leaving the file.
* Document *what* the function returns and *why* it might raise — not the
  obvious *how*.
* Surface non-obvious invariants (e.g. "rules-file values win over spec
  values") in the docstring of the function that enforces them.

### 2. The `design/` folder

Two files, both organized into the same three sections (parsing, generation,
customization):

* `design/REQUIREMENTS.md` — what the code must do. Each requirement is
  satisfied by a real module / function / template; new requirements only
  land here once the implementation is in place.
* `design/DESIGN.md` — how the code does it. Module map, data flow, key
  invariants, and decisions visible in the code.

Update both when you change subsystem behavior — drift between code and
either file is a bug. Both are reverse-engineered from the source, so
if you find prose contradicting the implementation, the doc is stale and a
fix is owed. Cite design facts by section number (`REQUIREMENTS §1.5`,
`DESIGN §2.4`) rather than copy-pasting prose.

### 3. The user docs site (`docs/`)

* `docs/` renders to <https://ffaraone.github.io/okapipy/> via mkdocs.
  Layout: `user-guide/` (installation, quick start, customization,
  rules, strategies, templates), `developer-guide/` (parser/generator
  internals at a high level), `reference/` (CLI + module references).
* The user docs and the design docs **must not duplicate each other**.
  When something is true of the user experience (CLI flags, generated
  client surface), document it in `docs/` and link from `design/` or
  `CLAUDE.md`. When something is an internal contract a maintainer needs
  to honor, keep it in `design/`.
* Examples in user docs must match what the generator actually emits.
  When you change templates or runtime APIs, sweep `docs/user-guide/`
  for stale snippets.

## Test conventions

These come from prior feedback rounds and are in force across the repo.
The generator suite follows the same conventions.

* **pytest functions only.** No `unittest` classes, no test class
  hierarchies. Every test has a docstring explaining *what* it's
  verifying — not just restating the function name.
* **Fixtures live in `conftest.py`**, not inline in test files. The
  parser suite's `english_nlp` session-scoped fixture loads spaCy from
  `./.spacy/`; an autouse `_reset_nlp_cache` clears the in-process cache
  between tests so loader paths stay observable.
* **Mocking uses `pytest_mock`** (the `mocker` fixture). No
  `from unittest.mock import ...`.
* **Coverage minimum is 90%** for the parser package; the suite
  currently sits at ~94%. New generator code targets ≥90% from day one.
* **Generated tests use `pytest-httpx`** (`httpx_mock` fixture) and
  `shape="dicts"` so the suite does not depend on dmcg's model output.
* OpenAPI fixtures live under `tests/fixtures/`. `pytest-httpserver`
  (`served_fixtures` fixture) serves them over HTTP for URL-source
  tests.
