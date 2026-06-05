"""Tests for stale-base-file pruning across regenerations.

When the spec loses a parser-tree node between two runs, the previous run's
base file for that node becomes orphaned. `write_to_disk` reads the prior
generated-state file, computes `previous.base_files - current` keys, and
deletes those files from disk. User-layer files are never pruned.
"""

from __future__ import annotations

from pathlib import Path

from okapipy.generator import generate_for_mount
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse


def _generate_and_write(spec_path: Path, output_dir: Path):
    api = parse(spec_path)
    vfs = generate_for_mount(
        api,
        raw_spec=spec_path,
        output_dir=output_dir,
        package="prune",
        client_class="PruneClient",
    )
    return write_to_disk(vfs, output_dir)


def test_first_run_prunes_nothing(
    orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """No previous state file → nothing to prune."""
    out = tmp_path / "out"

    report = _generate_and_write(orders_and_products_spec_file, out)

    assert report.pruned == []


def test_removed_collection_prunes_its_base_file(
    orders_and_products_spec_file: Path, orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """Removing `/products` from the spec deletes `base/collections/products.py`."""
    out = tmp_path / "out"
    _generate_and_write(orders_and_products_spec_file, out)
    products_base = out / "src" / "prune" / "base" / "collections" / "products.py"
    assert products_base.exists()

    report = _generate_and_write(orders_only_spec_file, out)

    assert "src/prune/base/collections/products.py" in report.pruned
    assert not products_base.exists()


def test_pruning_preserves_user_layer_stub_for_removed_node(
    orders_and_products_spec_file: Path, orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """User-layer files are never pruned, even when their base counterpart is gone."""
    out = tmp_path / "out"
    _generate_and_write(orders_and_products_spec_file, out)
    products_user = out / "src" / "prune" / "collections" / "products.py"
    assert products_user.exists()
    user_content = products_user.read_text()

    _generate_and_write(orders_only_spec_file, out)

    # User-layer file still on disk; content unchanged.
    assert products_user.exists()
    assert products_user.read_text() == user_content


def test_unchanged_spec_does_not_prune(
    orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """A no-op regen prunes nothing."""
    out = tmp_path / "out"
    _generate_and_write(orders_and_products_spec_file, out)

    report = _generate_and_write(orders_and_products_spec_file, out)

    assert report.pruned == []


def test_dry_run_reports_pruning_without_deleting(
    orders_and_products_spec_file: Path, orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """`dry_run=True` lists files that would be pruned but leaves them on disk."""
    out = tmp_path / "out"
    _generate_and_write(orders_and_products_spec_file, out)
    products_base = out / "src" / "prune" / "base" / "collections" / "products.py"

    api = parse(orders_only_spec_file)
    vfs = generate_for_mount(
        api,
        raw_spec=orders_only_spec_file,
        output_dir=out,
        package="prune",
        client_class="PruneClient",
    )
    report = write_to_disk(vfs, out, dry_run=True)

    assert "src/prune/base/collections/products.py" in report.pruned
    assert report.would_change is True
    assert products_base.exists()  # not deleted in dry-run
