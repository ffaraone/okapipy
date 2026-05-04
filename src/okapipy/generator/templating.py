"""Jinja2 environment factory and post-format hook.

Single environment per `generate()` call. Loader is `ChoiceLoader([user, packaged])`
when the user passes `templates_dir`, otherwise just the packaged loader. Rendered
Python files run through `ruff format` before they hit the virtual FS — non-Python
files pass through unchanged.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2 import (
    BaseLoader,
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    StrictUndefined,
    TemplateError,
)

from okapipy.generator.errors import (
    FormatError,
    TemplateRenderError,
    UnknownTemplateError,
)


def make_environment(templates_dir: Path | None) -> Environment:
    """Build the Jinja2 environment used for the entire generation run.

    `templates_dir` is searched first via `FileSystemLoader`; the packaged defaults
    in `okapipy.generator.templates` are searched second. `StrictUndefined` makes
    missing context variables fail loudly at render time rather than silently
    emitting `""`.
    """
    loaders: list[BaseLoader] = []
    if templates_dir is not None:
        loaders.append(FileSystemLoader(str(templates_dir)))
    loaders.append(PackageLoader("okapipy.generator", "templates"))
    env = Environment(  # nosec B701 — emits Python source, HTML autoescape would corrupt output
        loader=ChoiceLoader(loaders),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        autoescape=False,
        undefined=StrictUndefined,
    )
    env.filters["snake_case"] = snake_case
    env.filters["pascal_case"] = _pascal_case
    env.filters["kebab_case"] = _kebab_case
    env.filters["tojson"] = _tojson
    env.filters["py_repr"] = _py_repr
    env.filters["py_class_or_none"] = _py_class_or_none
    return env


def render(env: Environment, template_name: str, context: Mapping[str, Any]) -> str:
    """Render `template_name` against `context`, mapping Jinja errors to ours."""
    try:
        template = env.get_template(template_name)
    except TemplateError as exc:
        raise UnknownTemplateError(f"template not found: {template_name}") from exc
    try:
        return template.render(**context)
    except TemplateError as exc:
        raise TemplateRenderError(template_name, str(exc)) from exc


def render_python(
    env: Environment,
    template_name: str,
    context: Mapping[str, Any],
) -> str:
    """Render a Python template, isort-fix it, then `ruff format` the result.

    The isort pass uses `ruff check --fix --select I` so generated files don't
    accumulate `I001` lints when imported by users running ruff against their
    whole project. The format pass runs after isort so the final output is
    canonical.
    """
    rendered = render(env, template_name, context)
    sorted_ = ruff_isort(rendered, template_name)
    return ruff_format(sorted_, template_name)


def ruff_isort(source: str, label: str) -> str:
    """Run `ruff check --fix --select I` to apply isort to `source`."""
    try:
        result = subprocess.run(
            [
                "ruff",
                "check",
                "--fix",
                "--select",
                "I",
                "--stdin-filename",
                "generated.py",
                "-",
            ],
            input=source,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FormatError(label, "ruff is not installed") from exc
    if result.returncode != 0:
        raise FormatError(label, result.stderr)
    return result.stdout


def ruff_format(source: str, label: str) -> str:
    """Format `source` with `ruff format` via stdin/stdout.

    `label` is only used in error messages — it identifies the template that
    produced the source so the user knows where to look when ruff rejects it.
    """
    try:
        result = subprocess.run(
            ["ruff", "format", "--stdin-filename", "generated.py", "-"],
            input=source,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FormatError(label, "ruff is not installed") from exc
    if result.returncode != 0:
        raise FormatError(label, result.stderr)
    return result.stdout


_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def snake_case(value: str) -> str:
    """Convert PascalCase / camelCase / kebab-case input to snake_case."""
    normalized = value.replace("-", "_").replace(" ", "_")
    return _CAMEL_BOUNDARY.sub("_", normalized).lower()


def _pascal_case(value: str) -> str:
    """Convert any casing to PascalCase by routing through snake_case."""
    return "".join(part.capitalize() for part in snake_case(value).split("_") if part)


def _kebab_case(value: str) -> str:
    """Convert any casing to kebab-case (lowercase, hyphen-separated)."""
    return snake_case(value).replace("_", "-")


def _tojson(value: Any) -> str:
    """Render `value` as a JSON literal that round-trips through Python's parser.

    JSON literals (`null`, `true`, `false`, numbers, strings) are also valid Python
    expressions when treated as expressions only — but `null` is the exception.
    For Python output we want `None` / `True` / `False`, so we route through
    `_py_repr` instead. This filter is kept as the JSON-literal escape hatch.
    """
    return json.dumps(value)


def _py_repr(value: Any) -> str:
    """Render `value` as a Python literal that compiles back to the same value."""
    return repr(value)


def _py_class_or_none(value: str | None) -> str:
    """Render a model class name as a Python identifier, or `None` if unset.

    Generated code uses this to write `model_cls=Order` or `model_cls=None`
    inline in operation methods. The runtime `from_response` short-circuits when
    `model_cls is None` (returning the raw value), so passing `None` for an
    operation with no response schema is correct behavior.
    """
    if value is None:
        return "None"
    return value
