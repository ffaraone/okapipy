"""Generator error hierarchy.

`GenerationError` is the root; raise more specific subclasses for cases the user
can act on (a template that didn't render, a template name that wasn't found,
a ruff format failure carrying its stderr).
"""

from __future__ import annotations


class GenerationError(Exception):
    """Base class for every error raised by the generator."""


class UnknownTemplateError(GenerationError):
    """Raised when a referenced template name is not in the loader chain."""


class TemplateRenderError(GenerationError):
    """Raised when Jinja2 fails to render a template (StrictUndefined, syntax, etc.)."""

    def __init__(self, template_name: str, message: str) -> None:
        super().__init__(f"failed to render {template_name}: {message}")
        self.template_name = template_name


class FormatError(GenerationError):
    """Raised when `ruff format` rejects a rendered Python file."""

    def __init__(self, template_name: str, stderr: str) -> None:
        super().__init__(f"ruff format failed for {template_name}:\n{stderr}")
        self.template_name = template_name
        self.stderr = stderr
