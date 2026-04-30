"""Public generator entry point.

`generate(api, raw_spec, ...)` returns a virtual filesystem (`dict[str, str]`)
of the generated project. The CLI calls this and flushes to disk; tests inspect
the dict directly.

Phase 2 emits the static project skeleton (pyproject, README, LICENSE, gitignore,
.python-version, py.typed, empty `__init__.py` / `client.py` / `models.py`).
Subsequent phases populate the runtime library, models, client, namespaces,
collections, resources, actions, and tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from okapipy.generator.emit.client import emit_client
from okapipy.generator.emit.project import emit_project_skeleton
from okapipy.generator.emit.runtime import emit_runtime
from okapipy.generator.emit.walk import emit_root_init_extension, emit_tree
from okapipy.generator.models import emit_models, public_names
from okapipy.generator.templating import make_environment
from okapipy.parser.model import APIModel


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
) -> dict[str, str]:
    """Build the virtual FS for the generated client project.

    Args:
        api: parsed `APIModel` produced by `okapipy.parser.api.parse`.
        raw_spec: original OpenAPI document (path, URL string, or already-loaded dict).
            Forwarded to `datamodel-code-generator` for `models.py` emission.
        output_dir: target directory the CLI will flush the virtual FS into.
        package: dotted package path for the generated client (e.g. "acme.commerce").
        client_class: PascalCase class name for the sync client; async sibling is
            `Async<client_class>`.
        project_name: PEP 503 distribution name. Defaults to the last segment of
            `package`.
        project_version: initial version string emitted into `pyproject.toml`.
        python_version: pinned Python version for the generated project.
        license: SPDX identifier; drives the `LICENSE` placeholder.
        templates_dir: optional directory of user templates. Resolved before the
            packaged defaults (ChoiceLoader).
        model_templates_dir: optional directory of `datamodel-code-generator`
            templates. Forwarded as dmcg's `custom_template_dir`.

    Returns:
        A `dict[str, str]` mapping POSIX-style relative paths to file contents.
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
    }
    env = make_environment(templates_dir)
    vfs: dict[str, str] = {}
    vfs.update(emit_project_skeleton(env, project_context, package_path, top_package))
    # Phase 6 walker computes the top-level re-exports up front so the runtime
    # `__init__.py` can splice them into a single `__all__` literal.
    extra_imports, extra_public = emit_root_init_extension(api)
    # Phase 3: vendor the runtime. Builds the `__init__.py` that re-exports
    # both the runtime primitives and the top-level namespace/collection classes.
    vfs.update(emit_runtime(package_path, client_class, extra_imports, extra_public))
    # Phase 4: models from dmcg.
    models_source = emit_models(raw_spec, model_templates_dir, python_version)
    vfs[f"src/{package_path}/models.py"] = models_source
    available_models = public_names(models_source)
    # Phase 5: sync + async client classes.
    vfs.update(emit_client(env, project_context, package_path, api))
    # Phase 6: walk the parsed tree and emit one file per node.
    vfs.update(emit_tree(env, api, project_context, package_path, available_models))
    # Make the four subdirectories importable (only when they contain files).
    for subdir in ("namespaces", "collections", "resources", "actions"):
        if any(p.startswith(f"src/{package_path}/{subdir}/") for p in vfs):
            vfs.setdefault(f"src/{package_path}/{subdir}/__init__.py", "")
    return vfs
