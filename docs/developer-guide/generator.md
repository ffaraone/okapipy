# Generator internals

The generator turns the parser's `APIModel` tree into a runnable Python
project: a `pyproject.toml`, a vendored runtime, base-layer files for
every node, user-layer stubs, generated tests, and a manifest that
makes drift detection possible across runs.

The whole thing is structured as a **virtual filesystem** —
`dict[str, GeneratedFile]` keyed by POSIX-style relative path. The CLI
flushes that dict to disk; tests inspect it directly. No filesystem
side effects in the generator itself.

```
APIModel
   │
   ▼
generate(api, raw_spec, ...)        ← orchestration, in api.py
   │
   ├─►  emit_project_skeleton()     pyproject, README, LICENSE, .gitignore  (one-shot)
   ├─►  emit_runtime()              vendored runtime + base/__init__.py     (regenerated)
   ├─►  emit_models()               base/models.py via datamodel-code-gen   (regenerated)
   ├─►  emit_client()               sync + async ClientBase                 (regenerated)
   ├─►  emit_tree()                 one base/<node>.py per parser-tree node (regenerated)
   ├─►  emit_stubs()                user-layer subclass stubs               (one-shot)
   ├─►  emit_tests()                test scaffolding                        (one-shot)
   └─►  compute_manifest()          base/_manifest.json                     (regenerated)
   │
   ▼
dict[str, GeneratedFile]            ← virtual FS
   │
   ▼
write_to_disk(vfs, output_dir, dry_run=...)   ← in vfs.py; respects one_shot
```

## Lifecycle: one-shot vs. regenerated

Every `GeneratedFile` carries a `one_shot` flag.

* `one_shot=False` — files under `src/{package}/base/`. Rewritten on
  every run. Includes: vendored runtime, `models.py` from
  `datamodel-code-generator`, sync + async client base classes, one
  file per parser-tree node, `_manifest.json`.
* `one_shot=True` — files under `src/{package}/` (subclass stubs the
  customer customizes), the project skeleton (`pyproject.toml`,
  `README.md`, `LICENSE`, `.gitignore`, `.python-version`), and the
  generated test scaffolding.

`write_to_disk` honors the flag: existing one-shot files are **never**
overwritten; their absence is treated as "first generation, write the
stub". Base files are written unconditionally.

The order inside `generate()` matters:

1. **Project skeleton & runtime first** — so subsequent emitters can
   rely on the package layout.
2. **Models next** — the walker needs to know which model names are
   actually emitted (some specs reference schemas
   `datamodel-code-generator` can't represent), so it can drop dangling
   imports.
3. **Client + walker** — the walker is the bulk of the work, one base
   file per parser-tree node.
4. **User-layer stubs** — written one-shot, auto-wiring every
   `__<child>_factory__` for that subtree.
5. **Tests** — also one-shot, so customer edits to the suite survive.
6. **Manifest last** — captures the *full* set of base files. Used by
   drift detection and `--check`.

## The walker (`emit/walk.py`)

`emit_tree(env, api, project_context, package_path, available_models)`
walks the parser tree and emits one base file per node. The visitor
maintains a small context as it descends:

* The full breadcrumb of singular collection names (used for naming).
* The current namespace path (so files land in the right `base/<ns>/`
  subdirectory).
* The set of model names that actually exist in `models.py`. References
  to missing schemas degrade gracefully: the type becomes `Any` and the
  import is dropped.

For each node, the walker picks a Jinja template under
`generator/templates/package/...` and renders it with a context dict
that includes:

* The node itself.
* The factory hooks for its children (PascalCase class names).
* The list of operations and their method/path/request/response
  metadata.
* The set of imports needed (computed up front, deduped).

Templates have side-effect-free filters in `runtime/filters.py` and a
small templating layer (`templating.py`) that uses Jinja's
`ChoiceLoader` to look up user templates *first* (when `--templates-dir`
is passed) and packaged defaults second.

## Models (`models.py` + `datamodel-code-generator`)

`emit_models(raw_spec, model_templates_dir, python_version)` invokes
[datamodel-code-generator][dmcg] with:

* The raw spec (path, URL, or already-loaded dict).
* `pydantic-v2` output mode.
* Pinned Python version (`--target-python-version`) so f-string and
  match syntax matches what the rest of the project emits.
* Optional `model_templates_dir` forwarded as `--custom-template-dir`.

The result is a single Python source string written to
`src/{package}/base/models.py`.

`public_names(source)` parses the emitted source and returns the set of
top-level class names. The walker uses that set to validate model
references — anything not in the set becomes `Any` in the generated
client.

`--no-models` skips this step entirely. The walker then drops every
`from ..models import ...` line and replaces request / response model
types with `dict` and `None`. Operations end up untyped (raw dicts in /
out). This is an escape hatch for two situations:

* `datamodel-code-generator` can't process the spec's schemas (rare,
  but it happens with very baroque `oneOf` graphs).
* The consumer wants to bring their own model layer (e.g. they already
  have hand-written Pydantic types they prefer).

## The runtime (`runtime/` + `emit_runtime`)

A small set of files vendored into every generated client:

* `runtime/transport.py` — the `Transport` wrapper around `httpx.Client`
  / `httpx.AsyncClient`.
* `runtime/strategies.py` — pagination / filter / sort Protocols and
  the built-ins. (See [Strategies](../user-guide/strategies.md).)
* `runtime/filters.py` + `runtime/sort.py` — the small DSL used to
  *compose* filter trees and sort term lists at call time.
* `runtime/types.py` — small shared types (`PageOf[...]`, etc.).
* `runtime/exceptions.py` — `ConfigurationError`, `UnsupportedFilterError`,
  `UnsupportedSortError`, plus HTTP error mapping.

Vendoring (rather than depending on a separate `okapipy-runtime` PyPI
package) is intentional: it keeps the generated client self-contained
and lets us tighten the runtime API without breaking older clients.

## The manifest (`manifest.py` + `edges.py`)

`base/_manifest.json` records:

```json
{
  "okapipy_version": "0.1.0",
  "spec_hash": "<sha256>",
  "rules_hash": "<sha256>",
  "generated_at": "<ISO-8601>",
  "base_files": [...],
  "edges": [
    {"parent": "ClientBase", "child": "OrdersCollectionBase",
     "factory": "__orders_factory__", "user_module": "orders"},
    ...
  ]
}
```

* `base_files` drives **pruning**: any `base/*.py` file present on disk
  but absent from the new manifest is a stale leftover from a removed
  namespace/collection, and gets deleted on the next run.
* `edges` drives **drift detection**: each edge says "the parent's
  `__<factory>__` should point at the user-layer subclass in
  `<user_module>`." `vfs.py` checks the actual content of one-shot user
  files against the expected edges and emits a warning per missing
  factory binding.

`--check` is a dry-run mode that runs the full pipeline up to disk
write, then refuses to write *anything* and exits non-zero if any base
file would change, any drift warning fires, or any stale base file
would be pruned. CI gate.

## The VFS (`vfs.py`)

Two responsibilities:

```python
@dataclass
class GeneratedFile:
    content: str
    one_shot: bool = False

def write_to_disk(
    vfs: dict[str, GeneratedFile],
    output_dir: Path,
    *,
    dry_run: bool = False,
) -> WriteReport: ...
```

`write_to_disk`:

1. Reads the **previous manifest** (if any) under `output_dir`.
2. For each entry in the new VFS:
    * If `one_shot=True` and the target file already exists → **skip**
      (and don't even read it; we don't merge).
    * Otherwise, write unconditionally.
3. **Prune**: any file under `<package>/base/` that's listed in the
   *previous* manifest but not the *new* one → delete.
4. Compare the new VFS against the disk for **drift detection** on
   one-shot files (stubs missing `__<child>_factory__` bindings).
5. Return a `WriteReport` summarising `written`, `skipped`, `pruned`,
   `would_change`, and `warnings`.

When `dry_run=True`, no disk side effects happen — but the report is
populated as if it had. That's what powers `--check`.

## Templating (`templating.py`)

Templates live under `generator/templates/`. The packaging layout is:

```
templates/
├── package/        # one template per generated source file
│   ├── client.py.j2
│   ├── namespace.py.j2
│   ├── collection.py.j2
│   ├── resource.py.j2
│   ├── singleton.py.j2
│   ├── action.py.j2
│   ├── stub_*.py.j2     # user-layer stubs
│   └── ...
├── project/        # pyproject, README, LICENSE, .gitignore
├── tests/          # generated test scaffolding
└── model/          # datamodel-code-generator overrides
```

`make_environment(user_templates_dir)` builds a Jinja `Environment`
with a `ChoiceLoader`: user templates first (when `--templates-dir` is
passed), packaged defaults second. `StrictUndefined` makes missing
context variables fail loudly at render time. After rendering, every
Python file passes through `ruff check --fix --select I` (isort) and
then `ruff format` for canonical output.

User-facing template overrides are documented in
[Template customization](../user-guide/templates.md). When changing the
packaged templates, keep in mind that user overrides may be tracking
specific variables in the context dict — backwards-compatible renames
should keep the old variable around for a release or two before
removing it.

## Errors (`errors.py`)

* `GenerationError` — base class. CLI catches it and prints a friendly
  error.
* Sub-types for: missing required model, ambiguous schema name, dmcg
  failure, drift detection refusing to write under `--check`, and a few
  internal-consistency checks the walker performs as it goes.

`-vv` (DEBUG logging on the CLI root) prints full tracebacks; otherwise
only the message reaches stderr.

## Adding a new emitter

Most generator changes are *template* changes — new fields, renames,
better naming. Reach for a new emitter only when you're adding a
*new file* to the generated project (e.g. a new vendored runtime
module, or a new project-skeleton file).

Steps:

1. Add the template under `generator/templates/`.
2. Add the emitter under `generator/emit/`. It should accept the same
   shape as existing emitters: `(env, project_context, package_path,
   ...) -> dict[str, str]`.
3. Wire it into `generate()` in `api.py`, with the right `one_shot`
   flag.
4. Add a test under `tests/generator/` that:
    * Exercises a small parser tree.
    * Asserts the new file lands in the VFS at the expected path.
    * Asserts the lifecycle flag is correct.
    * (For one-shot files) asserts a second `generate(...)` with the
      file already in the VFS doesn't overwrite it.

Then run `uv run pytest tests/generator/` and `uv run mypy src` and
you're done.

[dmcg]: https://github.com/koxudaxi/datamodel-code-generator
