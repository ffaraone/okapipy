"""Render the project skeleton — pyproject, README, LICENSE, gitignore, etc."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jinja2 import Environment

from okapipy.generator.templating import render


def emit_project_skeleton(
    env: Environment,
    project_context: Mapping[str, Any],
    package_path: str,
    top_package: str,
) -> dict[str, str]:
    """Render the static project files into a virtual FS dict.

    Emits `pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`,
    `.python-version`, and `src/<package_path>/py.typed`. The user-layer
    `__init__.py` is emitted by `emit_stubs` (one-shot, empty). `client.py`
    and `models.py` are populated by the runtime / dmcg / walker phases.
    """
    ctx = {**project_context, "top_package": top_package}
    out: dict[str, str] = {
        "pyproject.toml": render(env, "project/pyproject.toml.jinja", ctx),
        "README.md": render(env, "project/README.md.jinja", ctx),
        "LICENSE": render(env, "project/LICENSE.jinja", ctx),
        ".gitignore": render(env, "project/gitignore.jinja", ctx),
        ".python-version": render(env, "project/python-version.jinja", ctx),
        f"src/{package_path}/py.typed": "",
    }
    return out
