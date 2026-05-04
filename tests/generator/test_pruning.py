"""Tests for stale-base-file pruning across regenerations.

When the spec loses a parser-tree node between two runs, the previous run's
base file for that node becomes orphaned. `write_to_disk` reads the prior
manifest, computes `previous.base_files - current` keys, and deletes those
files from disk. User-layer files are never pruned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse

SHRINK_BEFORE = """
openapi: 3.0.0
info: {title: Sample, version: 1.0.0}
paths:
  /orders:
    get:
      summary: List orders
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Order'}
  /products:
    get:
      summary: List products
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Product'}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
    Product: {type: object, properties: {id: {type: string}}}
"""

SHRINK_AFTER = """
openapi: 3.0.0
info: {title: Sample, version: 1.0.0}
paths:
  /orders:
    get:
      summary: List orders
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Order'}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
"""


@pytest.fixture
def before_spec(tmp_path: Path) -> Path:
    p = tmp_path / "before.yaml"
    p.write_text(SHRINK_BEFORE, encoding="utf-8")
    return p


@pytest.fixture
def after_spec(tmp_path: Path) -> Path:
    p = tmp_path / "after.yaml"
    p.write_text(SHRINK_AFTER, encoding="utf-8")
    return p


def _generate_and_write(spec_path: Path, output_dir: Path):
    api = parse(spec_path)
    vfs = generate(
        api,
        raw_spec=spec_path,
        output_dir=output_dir,
        package="prune",
        client_class="PruneClient",
    )
    return write_to_disk(vfs, output_dir)


def test_first_run_prunes_nothing(before_spec: Path, tmp_path: Path) -> None:
    """No previous manifest → nothing to prune."""
    out = tmp_path / "out"

    report = _generate_and_write(before_spec, out)

    assert report.pruned == []


def test_removed_collection_prunes_its_base_file(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """Removing `/products` from the spec deletes `base/collections/products.py`."""
    out = tmp_path / "out"
    _generate_and_write(before_spec, out)
    products_base = out / "src" / "prune" / "base" / "collections" / "products.py"
    assert products_base.exists()

    report = _generate_and_write(after_spec, out)

    assert "src/prune/base/collections/products.py" in report.pruned
    assert not products_base.exists()


def test_pruning_preserves_user_layer_stub_for_removed_node(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """User-layer files are never pruned, even when their base counterpart is gone."""
    out = tmp_path / "out"
    _generate_and_write(before_spec, out)
    products_user = out / "src" / "prune" / "collections" / "products.py"
    assert products_user.exists()
    user_content = products_user.read_text()

    _generate_and_write(after_spec, out)

    # User-layer file still on disk; content unchanged.
    assert products_user.exists()
    assert products_user.read_text() == user_content


def test_unchanged_spec_does_not_prune(before_spec: Path, tmp_path: Path) -> None:
    """A no-op regen prunes nothing."""
    out = tmp_path / "out"
    _generate_and_write(before_spec, out)

    report = _generate_and_write(before_spec, out)

    assert report.pruned == []


def test_dry_run_reports_pruning_without_deleting(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """`dry_run=True` lists files that would be pruned but leaves them on disk."""
    out = tmp_path / "out"
    _generate_and_write(before_spec, out)
    products_base = out / "src" / "prune" / "base" / "collections" / "products.py"

    api = parse(after_spec)
    vfs = generate(
        api,
        raw_spec=after_spec,
        output_dir=out,
        package="prune",
        client_class="PruneClient",
    )
    report = write_to_disk(vfs, out, dry_run=True)

    assert "src/prune/base/collections/products.py" in report.pruned
    assert report.would_change is True
    assert products_base.exists()  # not deleted in dry-run
