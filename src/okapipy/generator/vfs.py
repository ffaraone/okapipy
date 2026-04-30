"""Virtual filesystem with file-lifecycle metadata.

The generator builds a `dict[str, GeneratedFile]` keyed on POSIX-style paths
relative to the output directory. Each `GeneratedFile` carries the file's
content plus its lifecycle policy:

* `one_shot=False` (default): regenerated every run. `write_to_disk` always
  overwrites. Everything under `base/` is regenerated, plus `py.typed`.
* `one_shot=True`: emitted exactly once on first generation. `write_to_disk`
  skips paths that already exist on disk. User-layer subclass stubs and the
  project skeleton (`pyproject.toml`, `README.md`, etc.) use this lifecycle.

The CLI flushes via `write_to_disk`; tests inspect the dict directly via
`vfs[path].content`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GeneratedFile:
    """A single emitted file plus its lifecycle policy."""

    content: str
    one_shot: bool = False


def write_to_disk(vfs: dict[str, GeneratedFile], output_dir: Path) -> None:
    """Write every entry in `vfs` to disk under `output_dir`.

    Creates parent directories as needed; writes UTF-8. `one_shot=True` entries
    whose target path already exists are skipped silently — that's the
    affordance that lets users edit the generated user-layer stubs without
    fearing regeneration.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, file in vfs.items():
        destination = output_dir / relative_path
        if file.one_shot and destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(file.content, encoding="utf-8")
