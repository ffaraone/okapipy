# Generator internals

The generator turns the parser's `APIModel` tree into a runnable Python
project: a `pyproject.toml`, a vendored runtime, base-layer files for
every node, user-layer stubs, generated tests, and a generated-state
file that makes drift detection possible across runs.

The whole thing is structured as a **virtual filesystem** —
`dict[str, GeneratedFile]` keyed by POSIX-style relative path. The CLI
flushes that dict to disk; tests inspect it directly. No filesystem
side effects in the generator itself.

```
okapipy.yml
   │
   ▼
load_manifest(path)                 ← okapipy.manifest
   │
   ▼
generate(manifest)                  ← orchestration, in generator/api.py
   │
   ├─►  emit_project_skeleton()     pyproject, README, LICENSE, .gitignore  (one-shot)
   ├─►  compose.plan_mounts()       parse every specs[] entry → MountedSpec list
   │
   │   per mount:
   ├─►    emit_models()             base/<mount>/models.py via dmcg          (regenerated)
   ├─►    emit_tree()               base/<mount>/{ns,coll,res,sing,act}/...  (regenerated)
   ├─►    emit_mount_namespace()    base/<mount>/__init__.py — synthetic     (regenerated)
   │                                <Mount>MountBase class
   ├─►    emit_stubs()              user-layer subclass stubs for the mount  (one-shot)
   ├─►    emit_tests()              per-mount test scaffolding               (one-shot)
   │
   ├─►  emit_runtime()              vendored runtime + base/__init__.py      (regenerated)
   ├─►  emit_client()               sync + async <Client>Base composing      (regenerated)
   │                                every mount as a @cached_property
   ├─►  emit_stubs(emit_root=True)  project-wide user-layer __init__ +       (one-shot)
   │                                client.py + tests/conftest + test_client
   └─►  GeneratedState              base/_generated.json — base_files +      (regenerated)
                                    edges aggregated across every mount
   │
   ▼
dict[str, GeneratedFile]            ← virtual FS
   │
   ▼
write_to_disk(vfs, output_dir, dry_run=...)   ← in vfs.py; respects one_shot
```

A single-spec manifest with `namespace: ''` collapses every mention of
`base/<mount>/` to `base/` and produces the historical flat layout
byte-for-byte. `generate_for_mount(api, raw_spec, ...)` — the
single-spec building block exposed for tests and embedded callers —
is the same pipeline minus the `compose.plan_mounts()` and the
cross-mount composition.

## Lifecycle: one-shot vs. regenerated

Every `GeneratedFile` carries a `one_shot` flag.

* `one_shot=False` — files under `src/{package}/base/`. Rewritten on
  every run. Includes: vendored runtime, `models.py` from
  `datamodel-code-generator`, sync + async client base classes, one
  file per parser-tree node, `_generated.json`.
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

`emit_tree(env, api, project_context, package_path, available_models,
*, shape, mount_relpath)` walks the parser tree and emits one base
file per node. The visitor maintains a small context as it descends:

* The full breadcrumb of singular collection names (used for naming).
* The current namespace path (so files land in the right `base/<ns>/`
  subdirectory).
* The set of model names that actually exist in the current mount's
  `models.py`. References to missing schemas degrade gracefully: the
  type becomes `Any` and the import is dropped.
* The `mount_relpath` (`"users/"`, `"platform/users/"`, or `""` for
  the root mount), prepended to every emitted path so multi-mount
  projects land their trees under `base/<mount>/...`. The companion
  `runtime_dots(mount_relpath)` helper computes the relative-import
  dot prefix templates use to reach the shared base-level modules
  (`client.py`, `exceptions.py`, …) from deep inside a mount.

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
`src/{package}/base/<mount>/models.py` (one per mount; the root mount
collapses `<mount>/` away). Multi-mount projects invoke dmcg once per
spec so two services declaring an unrelated `User` schema can't
collide at the import level.

`public_names(source)` parses the emitted source and returns the set of
top-level class names. The walker uses that set to validate model
references — anything not in the set becomes `Any` in the generated
client.

`shape: dicts` (in `okapipy.yml`) skips this step entirely. The walker
then drops every `from ..models import ...` line and types every body
/ return as `dict[str, Any]`. The client base also drops the `shape=`
constructor option and `with_shape(...)` — there is nothing to switch
to. This is an escape hatch for two situations:

* `datamodel-code-generator` can't process the spec's schemas (rare,
  but it happens with very baroque `oneOf` graphs).
* The consumer wants to bring their own model layer (e.g. they already
  have hand-written Pydantic types they prefer).

`shape: models` keeps `models.py` but locks the runtime to validation:
the `shape=` constructor option and `with_shape(...)` are dropped, and
bodies / returns are typed strictly as the recovered model
(`Foo` / `Foo | None`) rather than admitting a `dict[str, Any]` arm.

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

## Multi-spec composition (`compose.py`)

A project manifest can declare multiple `specs[]` entries. Each one
parses independently into its own `APIModel`; the generator composes
the results into one Python package.

`compose.plan_mounts(manifest)` is the entry point: it walks
`manifest.specs[]`, parses each source with the per-spec inputs
(`rules`, `strip_prefix`, `unmatched`, `lang`), and returns a list of
`MountedSpec` records carrying the mount path tuple, the parsed
`APIModel`, the raw spec (for `datamodel-code-generator`), and the
entry's manifest index. Dotted mounts (more than one segment) raise
`GenerationError` here — the intermediate-namespace machinery is not
yet wired through `emit_client`.

`mount_segments("platform.users")` returns the tuple
`("platform", "users")`; `mount_relpath(("users",))` returns
`"users/"` (the trailing slash makes concatenation safe). The root
mount (`()`) maps to `""` everywhere, which is how every per-mount
emitter collapses to the historical flat layout for single-spec
root-mount manifests.

The synthetic **mount namespace** is a small `Namespace` node built by
`compose.synthesize_mount_namespace(mount)`: same children as the
spec's top-level, named after the mount's leaf segment. The walker
renders it through the existing `namespace.py.jinja` template (with
`is_mount_root=True` so the import section uses
`from .namespaces.X import …` instead of the standard `from .X
import …`), and the result lands at `base/<mount>/__init__.py` with
class name `<Mount>MountBase` — the `Mount` suffix disambiguates it
from any spec-internal `<Mount>NamespaceBase` that happens to share
the leaf name.

`compose.mount_class_name(mount_ns)` is the canonical helper for
`<Mount>MountBase`. Use it anywhere you'd otherwise reach for
`namespace_class(mount_ns)` on a synthetic mount node.

The client template grows a parallel `top_mount_namespaces` list
alongside the existing `top_namespaces` / `top_collections` / etc.
Mount accessors render as `@cached_property`s on `<Client>Base` (and
the async sibling), wired through a `__<mount>_factory__` ClassVar so
the user-layer mount subclass plugs in via the same factory-hook
mechanism every other tree edge uses.

## The generated-state file (`state.py` + `edges.py`)

`base/_generated.json` records:

```json
{
  "generator_version": "0.1.0",
  "generated_at": "2026-06-05T11:42:00Z",
  "base_files": [
    "src/acme/commerce/base/__init__.py",
    "src/acme/commerce/base/client.py",
    "src/acme/commerce/base/collections/orders.py",
    "..."
  ],
  "edges": [
    {
      "parent_module": "client.py",
      "factory_attr": "__orders_factory__",
      "child_user_class": "OrdersCollection",
      "child_user_module": "collections/orders.py"
    },
    "..."
  ]
}
```

* `base_files` drives **pruning**: any `base/*.py` file present on
  disk but absent from the new run is a stale leftover from a removed
  namespace/collection, and gets deleted on the next run. Multi-mount
  projects union the per-mount file lists into one set.
* `edges` drives **drift detection**: each edge says "the parent's
  `__<factory>__` should point at the user-layer subclass in
  `<child_user_module>`." `vfs.py` checks the actual content of
  one-shot user files against the expected edges and emits a warning
  per missing factory binding. Cross-mount edges (client → mount
  namespace) are recorded the same way as intra-mount edges, so
  drift on a removed `specs[]` entry surfaces identically to drift on
  a removed namespace.

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
