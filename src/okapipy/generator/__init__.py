"""okapipy code generator.

Consumes a parsed `APIModel` and emits a strongly-typed Python client project. See
`generator.md` for the spec and `generator_plan.md` for the implementation plan.
"""

from __future__ import annotations

from okapipy.generator.api import generate
from okapipy.generator.errors import (
    GenerationError,
    TemplateRenderError,
    UnknownTemplateError,
)

__all__ = [
    "GenerationError",
    "TemplateRenderError",
    "UnknownTemplateError",
    "generate",
]
