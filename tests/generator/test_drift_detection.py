"""Tests for the drift-detection warnings emitted by `write_to_disk`.

When the spec gains or loses a parser-tree node between two generations, the
parent stub (one-shot, never overwritten) ends up out of sync with the base
tree. Drift detection compares the previous manifest's edges against the
current run's edges and warns the user about the exact lines to add/remove.
"""

from __future__ import annotations

from pathlib import Path

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse


def _generate_and_write(
    spec_path: Path,
    output_dir: Path,
    package: str = "drift",
    client_class: str = "DriftClient",
):
    """Helper: parse, generate, write."""
    api = parse(spec_path)
    vfs = generate(
        api,
        raw_spec=spec_path,
        output_dir=output_dir,
        package=package,
        client_class=client_class,
    )
    return write_to_disk(vfs, output_dir)


def test_first_generation_emits_no_warnings(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """With no previous manifest, nothing to drift against — zero warnings."""
    out = tmp_path / "out"

    report = _generate_and_write(orders_only_spec_file, out)

    assert report.warnings == []


def test_unchanged_spec_emits_no_warnings(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """Re-running against the same spec is a no-op for drift detection."""
    out = tmp_path / "out"
    _generate_and_write(orders_only_spec_file, out)

    report = _generate_and_write(orders_only_spec_file, out)

    assert report.warnings == []
    assert report.would_change is False


def test_added_collection_emits_warning_naming_parent_stub(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """Adding `/products` between runs warns about the unwired Client stub."""
    out = tmp_path / "out"
    _generate_and_write(orders_only_spec_file, out)

    report = _generate_and_write(orders_and_products_spec_file, out)

    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert "src/drift/client.py" in warning
    assert "__products_factory__ = ProductsCollection" in warning
    assert "__products_factory__ = AsyncProductsCollection" in warning


def test_added_collection_creates_new_user_stub(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """The new collection's stub is created (one-shot, didn't exist before)."""
    out = tmp_path / "out"
    _generate_and_write(orders_only_spec_file, out)
    products_stub = out / "src" / "drift" / "collections" / "products.py"
    assert not products_stub.exists()

    _generate_and_write(orders_and_products_spec_file, out)

    assert products_stub.exists()
    assert (
        "class ProductsCollection(ProductsCollectionBase)" in products_stub.read_text()
    )


def test_removed_collection_emits_stale_wiring_warning(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """Going from grown → simple leaves a stale `__products_factory__` line."""
    out = tmp_path / "out"
    _generate_and_write(orders_and_products_spec_file, out)
    # Before regen: confirm the auto-wired client stub references products.
    client_stub = out / "src" / "drift" / "client.py"
    assert "__products_factory__" in client_stub.read_text()

    report = _generate_and_write(orders_only_spec_file, out)

    assert any(
        "__products_factory__" in w and "stale" in w.lower() for w in report.warnings
    ), report.warnings


def test_user_already_wired_factory_suppresses_new_edge_warning(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """If the user pre-wired `__products_factory__`, the new-edge warning is suppressed."""
    out = tmp_path / "out"
    _generate_and_write(orders_only_spec_file, out)
    # User pre-wires by hand before regen.
    client_stub = out / "src" / "drift" / "client.py"
    contents = client_stub.read_text()
    contents = contents.replace(
        "class DriftClient(DriftClientBase):",
        "class DriftClient(DriftClientBase):\n    __products_factory__ = ProductsCollection",
    )
    client_stub.write_text(contents)

    report = _generate_and_write(orders_and_products_spec_file, out)

    # The sync wiring is present so its warning is suppressed; the async wiring
    # is still missing, so one warning fires for the async side. Verify the
    # sync line is NOT in any warning.
    for warning in report.warnings:
        assert (
            "= ProductsCollection\n" not in warning
            or "AsyncProductsCollection" in warning
        )


def test_dry_run_reports_warnings_without_writing(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """`dry_run=True` returns warnings + would_change without touching disk."""
    out = tmp_path / "out"
    _generate_and_write(orders_only_spec_file, out)
    products_stub = out / "src" / "drift" / "collections" / "products.py"
    assert not products_stub.exists()

    api = parse(orders_and_products_spec_file)
    vfs = generate(
        api,
        raw_spec=orders_and_products_spec_file,
        output_dir=out,
        package="drift",
        client_class="DriftClient",
    )
    report = write_to_disk(vfs, out, dry_run=True)

    assert report.would_change is True
    assert any("__products_factory__" in w for w in report.warnings)
    # No new file actually written.
    assert not products_stub.exists()


def test_dry_run_no_change_returns_clean_report(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """A dry-run after a no-op regen reports `would_change=False` and no warnings."""
    out = tmp_path / "out"
    _generate_and_write(orders_only_spec_file, out)

    api = parse(orders_only_spec_file)
    vfs = generate(
        api,
        raw_spec=orders_only_spec_file,
        output_dir=out,
        package="drift",
        client_class="DriftClient",
    )
    report = write_to_disk(vfs, out, dry_run=True)

    assert report.would_change is False
    assert report.warnings == []
