"""Public generator entry point: build the virtual filesystem for a client project.

`generate(api, raw_spec, ...)` orchestrates every emitter in the package and
returns a `dict[str, GeneratedFile]` keyed by POSIX-style relative path. The CLI
flushes this dict to disk via `okapipy.generator.vfs.write_to_disk`; tests
inspect the dict directly without touching the filesystem.

Each entry carries a `one_shot` lifecycle flag that controls regeneration:

* `one_shot=False` (regenerated every run) — files under `src/{package}/base/`:
  the vendored runtime, `datamodel-code-generator`-emitted `models.py`, sync
  and async client base classes, one file per parser-tree node, and the
  `_generated.json` state file that tracks node-to-file edges across runs.
* `one_shot=True` (emitted once, then left alone) — files under
  `src/{package}/` (subclass stubs the customer customizes), the project
  skeleton (`pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`,
  `.python-version`), and the generated test scaffolding.

The order of operations inside `generate` matters: the project skeleton and
runtime emit first (so subsequent emitters can rely on the package layout),
the walker emits one base file per node, then the user-layer stubs and tests
fill in the customer-facing surface, and finally the generated-state file is
computed last so it captures the full set of base files.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from okapipy.generator import compose
from okapipy.generator.edges import compute_edges, compute_state
from okapipy.generator.emit.client import emit_client
from okapipy.generator.emit.project import emit_project_skeleton
from okapipy.generator.emit.runtime import emit_runtime
from okapipy.generator.emit.stubs import (
    ChildWiring,
    client_wirings,
    emit_stubs,
)
from okapipy.generator.emit.tests import emit_tests
from okapipy.generator.emit.walk import (
    ChildRef,
    emit_mount_namespace,
    emit_root_init_extension,
    emit_tree,
    factory_attr,
    namespace_accessor_docstring,
    node_one_line,
)
from okapipy.generator.models import emit_models, public_names
from okapipy.generator.state import (
    GENERATOR_VERSION,
    STATE_FILENAME,
    Edge,
    GeneratedState,
    serialize,
)
from okapipy.generator.templating import make_environment, snake_case
from okapipy.generator.vfs import GeneratedFile
from okapipy.manifest import GenerationManifest
from okapipy.parser.model import APIModel

Shape = Literal["auto", "models", "dicts"]

# SPDX identifiers the LICENSE template renders verbatim text for. Drives the
# `license = "..."` line in the generated `pyproject.toml`: hatchling validates
# this field as an SPDX expression, so we only emit it for recognised ids and
# omit it (rather than fail at `uv sync` time) for free-form values like
# `"Proprietary"`.
_SPDX_LICENSES = frozenset(
    {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "MPL-2.0"}
)


def generate_for_mount(
    api: APIModel,
    raw_spec: dict[str, Any] | str | Path,
    *,
    output_dir: Path | None = None,
    package: str,
    client_class: str,
    project_name: str | None = None,
    project_version: str = "0.1.0",
    python_version: str = "3.13",
    license: str = "Proprietary",
    author: str | None = None,
    templates_dir: Path | None = None,
    model_templates_dir: Path | None = None,
    shape: Shape = "auto",
) -> dict[str, GeneratedFile]:
    """Build the virtual FS for a single-mount generated client project.

    This is the lower-level entry point: it produces the full set of files
    for one OpenAPI spec mounted at the package root. The high-level
    manifest-driven entry point is `generate(manifest)`.

    Args:
        api: parsed `APIModel` produced by `okapipy.parser.api.parse`.
        raw_spec: original OpenAPI document (path, URL string, or already-loaded dict).
            Forwarded to `datamodel-code-generator` for `models.py` emission.
        output_dir: accepted for symmetry with `generate(manifest)`; not used.
        package: dotted package path for the generated client (e.g. "acme.commerce").
        client_class: PascalCase class name for the sync client; async sibling is
            `Async<client_class>`. Base classes carry an additional `Base` suffix.
        project_name: PEP 503 distribution name. Defaults to the last segment of
            `package`.
        project_version: initial version string emitted into `pyproject.toml`.
        python_version: pinned Python version for the generated project.
        license: SPDX identifier; drives the `LICENSE` placeholder.
        author: copyright holder for the generated `LICENSE` and PEP 621
            `authors` entry in `pyproject.toml`. When omitted, the LICENSE
            falls back to the project name and `pyproject.toml` omits the
            `authors` block.
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
        "license_is_spdx": license in _SPDX_LICENSES,
        "author": author,
        "current_year": date.today().year,
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
    # Generated-state file, computed last so `base_files` reflects the full
    # base tree. The state path itself is included in `base_files` so pruning
    # treats it like any other regenerated base file.
    state_path = f"src/{package_path}/base/{STATE_FILENAME}"
    base_files = sorted(
        [p for p in vfs if p.startswith(f"src/{package_path}/base/")] + [state_path]
    )
    state = compute_state(api, package, base_files)
    vfs[state_path] = GeneratedFile(content=serialize(state))
    return vfs


def generate(manifest: GenerationManifest) -> dict[str, GeneratedFile]:
    """Build the virtual FS for the project described by `manifest`.

    Walks `manifest.specs[]`, parses each OpenAPI document with the
    per-spec inputs declared in the manifest (`rules`, `strip_prefix`,
    `unmatched`, `lang`), and composes the result into one generated
    Python package: one `pyproject.toml`, one `<Client>Base`, one
    vendored runtime, one `_generated.json`. Each spec entry produces
    its own sub-tree at `base/<mount_path>/...` (or at the package root
    for the empty mount).

    Args:
        manifest: the project manifest produced by
            `okapipy.manifest.load_manifest`.

    Returns:
        A `dict[str, GeneratedFile]` keyed on POSIX-style relative paths.

    Raises:
        GenerationError: when a `specs[]` entry declares a dotted mount
            namespace (currently unsupported) or when the manifest fails
            cross-mount collision checks.
    """
    package_path = manifest.package.replace(".", "/")
    top_package = manifest.package.split(".", 1)[0]
    resolved_project_name = manifest.project_name or manifest.package.rsplit(".", 1)[-1]
    project_context = {
        "package": manifest.package,
        "client_class": manifest.client_class,
        "project_name": resolved_project_name,
        "project_version": manifest.project_version,
        "python_version": manifest.python_version,
        "license": manifest.license,
        "license_is_spdx": manifest.license in _SPDX_LICENSES,
        "author": manifest.author,
        "current_year": date.today().year,
        "shape": manifest.shape,
    }
    env = make_environment(manifest.templates_dir)
    vfs: dict[str, GeneratedFile] = {}

    # Project skeleton — one-shot, project-wide.
    skeleton = emit_project_skeleton(env, project_context, package_path, top_package)
    _wrap(vfs, skeleton, one_shot=True)

    # Plan + parse every spec entry once. Dotted mounts raise here.
    mounts = compose.plan_mounts(manifest)
    root_mount = next((m for m in mounts if not m.mount_path), None)
    root_api = root_mount.api if root_mount is not None else APIModel()
    edges: list[Edge] = []

    # Per-mount: models, tree, base subdir markers, optional mount-namespace
    # class, user-layer stubs, tests. None of these emit project-wide files.
    for mount in mounts:
        mrp = compose.mount_relpath(mount.mount_path)
        if manifest.shape == "dicts":
            mount_models: set[str] = set()
        else:
            models_source = emit_models(
                mount.raw_spec, manifest.model_templates_dir, manifest.python_version
            )
            vfs[f"src/{package_path}/base/{mrp}models.py"] = GeneratedFile(
                models_source
            )
            mount_models = public_names(models_source)
        tree_files = emit_tree(
            env,
            mount.api,
            project_context,
            package_path,
            mount_models,
            shape=manifest.shape,
            mount_relpath=mrp,
        )
        _wrap(vfs, tree_files, one_shot=False)
        for subdir in (
            "namespaces",
            "collections",
            "resources",
            "singletons",
            "actions",
        ):
            if any(
                p.startswith(f"src/{package_path}/base/{mrp}{subdir}/") for p in vfs
            ):
                vfs.setdefault(
                    f"src/{package_path}/base/{mrp}{subdir}/__init__.py",
                    GeneratedFile(""),
                )
        mount_ns = (
            compose.synthesize_mount_namespace(mount) if mount.mount_path else None
        )
        if mount_ns is not None:
            mount_init_files = emit_mount_namespace(
                env,
                mount_ns,
                project_context,
                package_path,
                mount_models,
                manifest.shape,
                mrp,
            )
            _wrap(vfs, mount_init_files, one_shot=False)
        vfs.update(
            emit_stubs(
                mount.api,
                manifest.package,
                package_path,
                manifest.client_class,
                mount_relpath=mrp,
                emit_root=False,
                mount_namespace=mount_ns,
            )
        )
        test_files = emit_tests(
            env,
            mount.api,
            project_context,
            top_package,
            mount_relpath=mrp,
            emit_root=False,
        )
        _wrap(vfs, test_files, one_shot=True)
        edges.extend(compute_edges(mount.api, manifest.package, mount_relpath=mrp))

    # Mount-namespace ChildRefs for the client.py top-level accessors plus
    # the cross-edges that wire the client to each mount namespace.
    top_mount_namespaces, mount_client_wirings, mount_edges = _build_mount_top_level(
        mounts, manifest.package
    )
    edges.extend(mount_edges)

    # Cross-mount re-exports for base/__init__.py (mirror the existing
    # root_init_extension behavior for the root mount's top level).
    extra_imports, extra_public = emit_root_init_extension(root_api)
    for mount in mounts:
        if not mount.mount_path:
            continue
        seg = snake_case(compose.mount_segment_name(mount.mount_path))
        cls = compose.mount_class_name(compose.synthesize_mount_namespace(mount))
        extra_imports.append(f"from .{seg} import {cls}, Async{cls}")
        extra_public.append(cls)
        extra_public.append(f"Async{cls}")

    # Vendored runtime + base/__init__.py — one set per project.
    runtime = emit_runtime(
        package_path, manifest.client_class, extra_imports, extra_public
    )
    _wrap(vfs, runtime, one_shot=False)

    # base/client.py — composes root_api top-level + mount namespaces.
    client_files = emit_client(
        env,
        project_context,
        package_path,
        root_api,
        top_mount_namespaces=top_mount_namespaces,
    )
    _wrap(vfs, client_files, one_shot=False)

    # Project-wide user-layer __init__.py + client.py. We pass `root_api`
    # (its top-level wirings were already added to mount_client_wirings via
    # `_build_mount_top_level`) but with `emit_root=True` so emit_stubs
    # produces only the project-wide files; per-mount walks were already
    # done above for every mount including the root one.
    project_wirings = client_wirings(root_api, manifest.package) + mount_client_wirings
    vfs.update(
        emit_stubs(
            APIModel(),
            manifest.package,
            package_path,
            manifest.client_class,
            mount_relpath="",
            emit_root=True,
            client_wirings_override=project_wirings,
        )
    )
    project_test_files = emit_tests(
        env,
        APIModel(),
        project_context,
        top_package,
        mount_relpath="",
        emit_root=True,
    )
    _wrap(vfs, project_test_files, one_shot=True)

    # Generated-state file, computed last.
    state_path = f"src/{package_path}/base/{STATE_FILENAME}"
    base_files = sorted(
        [p for p in vfs if p.startswith(f"src/{package_path}/base/")] + [state_path]
    )
    state = GeneratedState(
        generator_version=GENERATOR_VERSION,
        generated_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        base_files=base_files,
        edges=sorted(
            set(edges),
            key=lambda e: (e.parent_module, e.factory_attr),
        ),
    )
    vfs[state_path] = GeneratedFile(content=serialize(state))
    return vfs


def _build_mount_top_level(
    mounts: list[compose.MountedSpec],
    package: str,
) -> tuple[list[ChildRef], list[ChildWiring], list[Edge]]:
    """Build the top-level mount-namespace artifacts the client needs.

    Returns three parallel views of the mount-namespace set:

    * `top_mount_namespaces` — `ChildRef`s the client template renders as
      `@cached_property` accessors plus `from .<mount> import ...` lines.
    * `mount_client_wirings` — user-layer `ChildWiring`s that the project
      `client.py` stub uses to point each `__<mount>_factory__` at the
      user-layer mount-namespace subclass.
    * `mount_edges` — generated-state `Edge`s for the client → mount
      wiring, so drift detection picks up a removed mount the same way
      it picks up a removed namespace.
    """
    refs: list[ChildRef] = []
    wirings: list[ChildWiring] = []
    edges: list[Edge] = []
    for mount in mounts:
        if not mount.mount_path:
            continue
        mount_ns = compose.synthesize_mount_namespace(mount)
        seg = snake_case(compose.mount_segment_name(mount.mount_path))
        cls = compose.mount_class_name(mount_ns)
        attr = snake_case(mount_ns.name)
        fattr = factory_attr(attr)
        refs.append(
            ChildRef(
                attr=attr,
                class_name=cls,
                module=seg,
                factory_attr=fattr,
                docstring=namespace_accessor_docstring(mount_ns),
                one_line=node_one_line(
                    mount_ns.summary,
                    mount_ns.description,
                    fallback=f"Namespace `{mount_ns.name}`.",
                ),
            )
        )
        wirings.append(
            ChildWiring(
                factory_attr=fattr,
                user_class=cls.removesuffix("Base"),
                user_module_path=f"{package}.{seg}",
            )
        )
        edges.append(
            Edge(
                parent_module="client.py",
                factory_attr=fattr,
                child_user_class=cls.removesuffix("Base"),
                child_user_module=f"{seg}/__init__.py",
            )
        )
    return refs, wirings, edges


def _wrap(
    vfs: dict[str, GeneratedFile],
    files: dict[str, str],
    *,
    one_shot: bool,
) -> None:
    """Promote a `dict[str, str]` from a sub-emitter into the lifecycle-tagged VFS."""
    for path, content in files.items():
        vfs[path] = GeneratedFile(content=content, one_shot=one_shot)
