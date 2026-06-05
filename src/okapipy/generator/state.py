"""Cross-run generated-state file: tracks regenerated files and parser-tree edges.

The state file is written to `src/{package_path}/base/_generated.json` on
every generation. It is named `_generated.json` (rather than the historical
`_manifest.json`) so it is not confused with the user-authored project
manifest read by `okapipy.manifest`. It serves two operational purposes:

* **Pruning.** `base_files` is the set of files the regenerated tree owns. On
  the next generation, `previous.base_files - current.base_files` is the set
  of stale files (their parser-tree node no longer exists in the spec) that
  `write_to_disk` deletes from disk. User-layer files are never tracked here
  and never pruned.
* **Drift detection.** `edges` is the set of parent → child wirings the
  current parser tree implies. On the next generation,
  `current.edges - previous.edges` are NEW children whose user-layer parent
  stub (one-shot, never overwritten) doesn't yet wire them; the difference
  is converted to warnings telling the user the exact one-line edit to make.

Edges are stored *abstractly* — one entry per sync/async pair, not per
emitted Python class. The drift-warning formatter expands one Edge into the
two lines (sync + async) the user needs to add.

This module is intentionally narrow: it owns the dataclasses and the
JSON-on-disk format, and nothing else. The graph-walking logic that produces
edges from a parsed `APIModel` lives in `okapipy.generator.edges`, kept apart
so that `state.py` does not depend on `emit/stubs.py` (which itself
depends on `vfs.py`, which depends back on `state.py`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _generator_version() -> str:
    """Return the installed okapipy version, falling back to `0.0.0+unknown`.

    Resolved at import time from package metadata so the state file always
    carries the version of the okapipy that produced it. The fallback covers
    running against a source tree that hasn't been installed (e.g. some
    packaging tests).
    """
    try:
        return version("okapipy")
    except PackageNotFoundError:
        return "0.0.0+unknown"


GENERATOR_VERSION = _generator_version()
"""Version string written into every generated-state file. Sourced from package metadata."""

STATE_FILENAME = "_generated.json"
"""Filename inside `<package>/base/` where the generated-state file is stored."""


@dataclass(frozen=True)
class Edge:
    """One parent → child wiring in the parser tree.

    `parent_module` is the user-layer relative path (`client.py`,
    `namespaces/commerce.py`, `collections/orders.py`, …) — i.e. where the
    drift detector looks to check if the user has wired the factory.
    `child_user_class` is the *sync* user-layer class name; the async sibling
    is implicit (`Async` + the same name).
    """

    parent_module: str
    factory_attr: str
    child_user_class: str
    child_user_module: str


@dataclass(frozen=True)
class GeneratedState:
    """The full generated-state record written under `base/_generated.json`."""

    generator_version: str
    generated_at: str
    base_files: list[str]
    edges: list[Edge]


def serialize(state: GeneratedState) -> str:
    """Render the generated-state record as a deterministic JSON string."""
    payload = asdict(state)
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def parse(text: str) -> GeneratedState:
    """Parse a state-file JSON string into a `GeneratedState`. Tolerant of unknown keys."""
    data = json.loads(text)
    edges = [Edge(**e) for e in data.get("edges", [])]
    return GeneratedState(
        generator_version=data.get("generator_version", ""),
        generated_at=data.get("generated_at", ""),
        base_files=list(data.get("base_files", [])),
        edges=edges,
    )


def read_from_disk(state_path: Path) -> GeneratedState | None:
    """Read a generated-state file from disk; return None when the file does not exist."""
    if not state_path.exists():
        return None
    try:
        return parse(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
