"""Public generator entry point: build the virtual filesystem for a client project.

`generate(api, raw_spec, ...)` orchestrates every emitter in the package and
returns a `dict[str, GeneratedFile]` keyed by POSIX-style relative path. The CLI
flushes this dict to disk via `okapipy.generator.vfs.write_to_disk`; tests
inspect the dict directly without touching the filesystem.

Each entry carries a `one_shot` lifecycle flag that controls regeneration:

* `one_shot=False` (regenerated every run) — files under `src/{package}/base/`:
  the vendored runtime, `datamodel-code-generator`-emitted `models.py`, sync
  and async client base classes, one file per parser-tree node, and the
  `_manifest.json` that tracks node-to-file edges across runs.
* `one_shot=True` (emitted once, then left alone) — files under
  `src/{package}/` (subclass stubs the customer customizes), the project
  skeleton (`pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`,
  `.python-version`), and the generated test scaffolding.

The order of operations inside `generate` matters: the project skeleton and
runtime emit first (so subsequent emitters can rely on the package layout),
the walker emits one base file per node, then the user-layer stubs and tests
fill in the customer-facing surface, and finally the manifest is computed
last so it captures the full set of base files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from okapipy.generator.edges import compute_manifest
from okapipy.generator.emit.client import emit_client
from okapipy.generator.emit.project import emit_project_skeleton
from okapipy.generator.emit.runtime import emit_runtime
from okapipy.generator.emit.stubs import emit_stubs
from okapipy.generator.emit.tests import emit_tests
from okapipy.generator.emit.walk import emit_root_init_extension, emit_tree
from okapipy.generator.manifest import MANIFEST_FILENAME, serialize
from okapipy.generator.models import emit_models, public_names
from okapipy.generator.templating import make_environment
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.model import APIModel

Shape = Literal["auto", "models", "dicts"]


def generate(
    api: APIModel,
    raw_spec: dict[str, Any] | str | Path,
    *,
    output_dir: Path,
    package: str,
    client_class: str,
    project_name: str | None = None,
    project_version: str = "0.1.0",
    python_version: str = "3.13",
    license: str = "Proprietary",
    templates_dir: Path | None = None,
    model_templates_dir: Path | None = None,
    shape: Shape = "auto",
) -> dict[str, GeneratedFile]:
    """Build the virtual FS for the generated client project.

    Args:
        api: parsed `APIModel` produced by `okapipy.parser.api.parse`.
        raw_spec: original OpenAPI document (path, URL string, or already-loaded dict).
            Forwarded to `datamodel-code-generator` for `models.py` emission.
        output_dir: target directory the CLI will flush the virtual FS into.
        package: dotted package path for the generated client (e.g. "acme.commerce").
        client_class: PascalCase class name for the sync client; async sibling is
            `Async<client_class>`. Base classes carry an additional `Base` suffix.
        project_name: PEP 503 distribution name. Defaults to the last segment of
            `package`.
        project_version: initial version string emitted into `pyproject.toml`.
        python_version: pinned Python version for the generated project.
        license: SPDX identifier; drives the `LICENSE` placeholder.
        templates_dir: optional directory of user templates. Resolved before the
            packaged defaults (ChoiceLoader).
        model_templates_dir: optional directory of `datamodel-code-generator`
            templates. Forwarded as dmcg's `custom_template_dir`.
        shape: response-shape policy for the generated client.

            * `"auto"` (default) — emit a dual-shape client. The constructor
              accepts a `shape: "models" | "dicts"` keyword and `with_shape(...)`
              returns a sibling switching shape at runtime. Bodies and returns
              are typed as `Foo | dict[str, Any]` / `Foo | dict[str, Any] | None`.
            * `"models"` — lock the client to typed Pydantic models. The
              `shape=` constructor option and `with_shape(...)` are dropped.
              Bodies are typed as `Foo`; returns as `Foo | None`.
            * `"dicts"` — lock the client to raw dicts. `base/models.py` is
              skipped, model imports are dropped, and bodies / returns are
              typed as `dict[str, Any]` / `dict[str, Any] | None`.

    Returns:
        A `dict[str, GeneratedFile]` mapping POSIX-style relative paths to
        file content + lifecycle policy.
    """
    del output_dir
    package_path = package.replace(".", "/")
    top_package = package.split(".", 1)[0]
    resolved_project_name = project_name or package.rsplit(".", 1)[-1]
    project_context = {
        "package": package,
        "client_class": client_class,
        "project_name": resolved_project_name,
        "project_version": project_version,
        "python_version": python_version,
        "license": license,
        "shape": shape,
    }
    env = make_environment(templates_dir)
    vfs: dict[str, GeneratedFile] = {}
    # Project skeleton — marked one-shot so customer edits to pyproject etc. survive.
    skeleton = emit_project_skeleton(env, project_context, package_path, top_package)
    _wrap(vfs, skeleton, one_shot=True)
    # Walker computes the top-level re-exports up front so the runtime
    # `__init__.py` can splice them into a single `__all__` literal.
    extra_imports, extra_public = emit_root_init_extension(api)
    # Vendor the runtime + build base/__init__.py (re-exports `*Base` classes).
    runtime = emit_runtime(package_path, client_class, extra_imports, extra_public)
    _wrap(vfs, runtime, one_shot=False)
    # Models from dmcg, regenerated. The dicts shape skips this step entirely;
    # the walker then drops every `from ..models import ...` line and types
    # every body / return as `dict[str, Any]` (or `Any` when no schema name
    # was recovered).
    if shape == "dicts":
        available_models: set[str] = set()
    else:
        models_source = emit_models(raw_spec, model_templates_dir, python_version)
        vfs[f"src/{package_path}/base/models.py"] = GeneratedFile(models_source)
        available_models = public_names(models_source)
    # Sync + async client base classes.
    client_files = emit_client(env, project_context, package_path, api)
    _wrap(vfs, client_files, one_shot=False)
    # Walk the parser tree → one base file per node.
    tree_files = emit_tree(
        env, api, project_context, package_path, available_models, shape=shape
    )
    _wrap(vfs, tree_files, one_shot=False)
    # Empty markers on each populated base subdirectory so `from ..namespaces`
    # imports resolve.
    for subdir in ("namespaces", "collections", "resources", "singletons", "actions"):
        if any(p.startswith(f"src/{package_path}/base/{subdir}/") for p in vfs):
            path = f"src/{package_path}/base/{subdir}/__init__.py"
            vfs.setdefault(path, GeneratedFile(""))
    # User-layer subclass stubs — already marked one-shot internally.
    vfs.update(emit_stubs(api, package, package_path, client_class))
    # Generated test scaffolding — one-shot so customer edits survive regeneration.
    test_files = emit_tests(env, api, project_context, top_package)
    _wrap(vfs, test_files, one_shot=True)
    # Manifest, computed last so `base_files` reflects the full base tree.
    # The manifest path itself is included in `base_files` so pruning treats
    # it like any other regenerated base file.
    manifest_path = f"src/{package_path}/base/{MANIFEST_FILENAME}"
    base_files = sorted(
        [p for p in vfs if p.startswith(f"src/{package_path}/base/")] + [manifest_path]
    )
    manifest = compute_manifest(api, package, base_files)
    vfs[manifest_path] = GeneratedFile(content=serialize(manifest))
    return vfs


def _wrap(
    vfs: dict[str, GeneratedFile],
    files: dict[str, str],
    *,
    one_shot: bool,
) -> None:
    """Promote a `dict[str, str]` from a sub-emitter into the lifecycle-tagged VFS."""
    for path, content in files.items():
        vfs[path] = GeneratedFile(content=content, one_shot=one_shot)
