"""okapipy code generator: turn a parsed `APIModel` into a Python client project.

`generate(api, raw_spec, ...)` takes the structural tree produced by
`okapipy.parser.parse` and returns a virtual filesystem (`dict[str, GeneratedFile]`)
describing every file of a self-contained, strongly-typed Python client.
`okapipy.generator.vfs.write_to_disk` flushes that VFS to a target directory.

The generated project is laid out in two cooperating layers:

* **Base layer** (`src/{package}/base/...`) — fully regenerated on every run.
  Owns the vendored runtime (transport, pagination/filter/sort strategies,
  exceptions), the `datamodel-code-generator`-emitted `models.py`, the sync and
  async client base classes, and one base file per node of the parser tree
  (namespace, collection, resource, singleton, action). Customers must not edit
  these files; the next run will overwrite them.
* **User layer** (`src/{package}/...`) — emitted exactly once. Pure subclass
  stubs (`class X(XBase): pass`) plus an empty package `__init__.py`. Customers
  add their overrides here; subsequent generator runs leave these files alone.

Project skeleton files (`pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`,
`.python-version`) are also one-shot so customer edits to dependency lists or
project metadata survive regeneration.
"""

from __future__ import annotations

from okapipy.generator.api import Shape, generate, generate_for_mount
from okapipy.generator.errors import (
    GenerationError,
    ManifestFormatError,
    ManifestNotFoundError,
    TemplateRenderError,
    UnknownTemplateError,
)

__all__ = [
    "GenerationError",
    "ManifestFormatError",
    "ManifestNotFoundError",
    "Shape",
    "TemplateRenderError",
    "UnknownTemplateError",
    "generate",
    "generate_for_mount",
]
