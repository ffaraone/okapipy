"""Virtual filesystem with file-lifecycle metadata, manifest pruning, and drift detection.

The generator builds a `dict[str, GeneratedFile]` keyed on POSIX-style paths
relative to the output directory. Each `GeneratedFile` carries the file's
content plus its lifecycle policy:

* `one_shot=False` (default): regenerated every run. `write_to_disk` always
  overwrites. Everything under `base/` plus `py.typed` falls in this bucket.
* `one_shot=True`: emitted exactly once on first generation. `write_to_disk`
  skips paths that already exist. User-layer stubs and the project skeleton
  use this lifecycle.

`write_to_disk` returns a `WriteReport`:

* **Pruning.** Reads the previous `base/_manifest.json` from disk; deletes
  any base file in `previous.base_files` that isn't in the current VFS.
  User-layer files are never pruned.
* **Drift detection.** Compares previous and current manifest edges; emits
  one warning per new/removed wiring on a one-shot user-layer parent that
  needs a manual edit.
* **Dry-run.** `dry_run=True` computes the report without touching disk;
  `WriteReport.would_change` is `True` if anything would change. Powers
  `okapipy generate --check`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from okapipy.generator.manifest import (
    MANIFEST_FILENAME,
    Edge,
    Manifest,
    read_from_disk,
)
from okapipy.generator.manifest import (
    parse as parse_manifest,
)


@dataclass(frozen=True)
class GeneratedFile:
    """A single emitted file plus its lifecycle policy."""

    content: str
    one_shot: bool = False


@dataclass(frozen=True)
class WriteReport:
    """Outcome of a `write_to_disk` call.

    `would_change` is `True` if writing would alter the on-disk state in any
    way (a base file's content differs, a one-shot file is missing, or a stale
    file would be pruned). The manifest itself is excluded from this check
    because its `generated_at` timestamp differs on every run.
    """

    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    would_change: bool = False


def write_to_disk(
    vfs: dict[str, GeneratedFile],
    output_dir: Path,
    *,
    dry_run: bool = False,
) -> WriteReport:
    """Flush `vfs` to `output_dir`. Prune stale base files. Emit drift warnings.

    Args:
        vfs: the generator's virtual filesystem.
        output_dir: target directory the project is being written into.
        dry_run: when `True`, no files are written, deleted, or modified —
            the returned report reflects what *would* happen.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path_rel = _find_manifest_path(vfs)

    # Snapshot pre-run on-disk state for drift detection.
    previous_manifest: Manifest | None = None
    if manifest_path_rel is not None:
        previous_manifest = read_from_disk(output_dir / manifest_path_rel)

    # Drift warnings (computed BEFORE writing so on-disk state is pre-run).
    warnings: list[str] = []
    if previous_manifest is not None and manifest_path_rel is not None:
        current_manifest = parse_manifest(vfs[manifest_path_rel].content)
        warnings = _compute_drift_warnings(
            previous=previous_manifest,
            current=current_manifest,
            output_dir=output_dir,
            manifest_path=manifest_path_rel,
        )

    # Write or skip VFS entries.
    written: list[str] = []
    skipped: list[str] = []
    would_change = False
    for path, file in vfs.items():
        target = output_dir / path
        if file.one_shot and target.exists():
            skipped.append(path)
            continue
        if path != manifest_path_rel and _content_differs(target, file.content):
            would_change = True
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file.content, encoding="utf-8")
        written.append(path)

    # Prune stale base files.
    pruned: list[str] = []
    if previous_manifest is not None:
        new_base = {p for p in vfs if "/base/" in p}
        for stale in sorted(set(previous_manifest.base_files) - new_base):
            target = output_dir / stale
            if target.exists():
                pruned.append(stale)
                if not dry_run:
                    target.unlink()
    if pruned:
        would_change = True

    return WriteReport(
        written=written,
        skipped=skipped,
        pruned=pruned,
        warnings=warnings,
        would_change=would_change,
    )


def _find_manifest_path(vfs: dict[str, GeneratedFile]) -> str | None:
    """Return the VFS key for `_manifest.json` (one expected) or None."""
    suffix = f"/base/{MANIFEST_FILENAME}"
    for path in vfs:
        if path.endswith(suffix):
            return path
    return None


def _content_differs(target: Path, content: str) -> bool:
    """Return `True` when the target doesn't exist or its bytes differ from `content`."""
    if not target.exists():
        return True
    try:
        return target.read_text(encoding="utf-8") != content
    except OSError:
        return True


def _compute_drift_warnings(
    *,
    previous: Manifest,
    current: Manifest,
    output_dir: Path,
    manifest_path: str,
) -> list[str]:
    """Return one warning per new/removed edge that needs a user-layer edit.

    New edges fire a warning only when their user-layer parent stub already
    exists on disk (otherwise the stub is about to be created with the new
    auto-wiring). Removed edges fire when the still-present parent stub
    references the now-stale child class.
    """
    prev_set = set(previous.edges)
    curr_set = set(current.edges)
    new_edges = sorted(
        curr_set - prev_set, key=lambda e: (e.parent_module, e.factory_attr)
    )
    removed_edges = sorted(
        prev_set - curr_set, key=lambda e: (e.parent_module, e.factory_attr)
    )

    # The user-layer root: strip `base/_manifest.json` from the manifest path.
    user_root = manifest_path.removesuffix(f"base/{MANIFEST_FILENAME}")

    warnings: list[str] = []
    for edge in new_edges:
        parent_path = output_dir / user_root / edge.parent_module
        if not parent_path.exists():
            continue  # Stub will be auto-wired; no warning needed.
        content = parent_path.read_text(encoding="utf-8")
        if f"{edge.factory_attr} = {edge.child_user_class}" in content:
            continue  # Already wired (by hand or by an earlier auto-wire).
        warnings.append(_format_new_edge_warning(edge, user_root))

    for edge in removed_edges:
        parent_path = output_dir / user_root / edge.parent_module
        if not parent_path.exists():
            continue
        content = parent_path.read_text(encoding="utf-8")
        if edge.factory_attr in content:
            warnings.append(_format_stale_edge_warning(edge, user_root))

    return warnings


def _format_new_edge_warning(edge: Edge, user_root: str) -> str:
    """Render the warning for a new child not yet wired in its parent stub."""
    parent = f"{user_root}{edge.parent_module}"
    return (
        f"new child not yet wired in {parent}\n"
        f"  add to the sync class:  {edge.factory_attr} = {edge.child_user_class}\n"
        f"  add to the async class: "
        f"{edge.factory_attr} = Async{edge.child_user_class}\n"
        f"  until you do, the new child resolves to its `Base` default rather "
        f"than your subclass."
    )


def _format_stale_edge_warning(edge: Edge, user_root: str) -> str:
    """Render the warning for a parent stub that still references a removed child."""
    parent = f"{user_root}{edge.parent_module}"
    return (
        f"stale wiring in {parent}\n"
        f"  {edge.factory_attr} references {edge.child_user_class} but the "
        f"spec no longer defines that child.\n"
        f"  remove the line and consider deleting "
        f"{user_root}{edge.child_user_module}."
    )
