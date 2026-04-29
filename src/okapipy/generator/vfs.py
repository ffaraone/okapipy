"""Virtual filesystem helpers.

The generator builds a `dict[str, str]` keyed on POSIX-style paths relative to the
output directory. The CLI flushes the dict to disk; tests inspect it directly.
"""

from __future__ import annotations

from pathlib import Path


def write_to_disk(vfs: dict[str, str], output_dir: Path) -> None:
    """Write every entry in `vfs` to disk under `output_dir`.

    Creates parent directories as needed; writes UTF-8; overwrites existing files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, content in vfs.items():
        destination = output_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
