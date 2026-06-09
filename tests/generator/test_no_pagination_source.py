"""Source-level tests on the collection module generated for non-paginated fetches.

These tests inspect the rendered Python source the generator produces for
a collection whose fetch operation has `pagination_supported=False`. They
complement the runtime e2e tests by pinning what is (and is not) emitted
into the generated file, so a regression that re-introduces strategy
plumbing or the page-walking methods is caught at the source layer.
"""

from __future__ import annotations

from pathlib import Path

from okapipy.generator import generate_for_mount
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.api import parse as parse_spec


def _items_collection_source(spec_path: Path, output_dir: Path) -> str:
    """Generate the no-pagination fixture and return the items collection source."""
    api = parse_spec(spec_path)
    vfs: dict[str, GeneratedFile] = generate_for_mount(
        api,
        raw_spec=spec_path,
        output_dir=output_dir,
        package="nopag",
        client_class="NoPag",
        project_name="nopag",
    )
    items_path = "src/nopag/base/collections/items.py"
    assert items_path in vfs, f"expected {items_path} in vfs, saw {sorted(vfs)}"
    return vfs[items_path].content


def test_collection_source_drops_get_page_method(
    no_pagination_fixture_path: Path, tmp_path: Path
) -> None:
    """The emitted collection source does not declare `get_page`."""
    source = _items_collection_source(no_pagination_fixture_path, tmp_path / "out")

    assert "def get_page(" not in source


def test_collection_source_drops_page_size_method(
    no_pagination_fixture_path: Path, tmp_path: Path
) -> None:
    """The emitted collection source does not declare `page_size`."""
    source = _items_collection_source(no_pagination_fixture_path, tmp_path / "out")

    assert "def page_size(" not in source


def test_collection_source_drops_iterator_class(
    no_pagination_fixture_path: Path, tmp_path: Path
) -> None:
    """No `<Coll>Iterator` / `Async<Coll>Iterator` class is emitted."""
    source = _items_collection_source(no_pagination_fixture_path, tmp_path / "out")

    assert "class ItemsCollectionBaseIterator" not in source
    assert "class AsyncItemsCollectionBaseIterator" not in source


def test_collection_source_drops_unsupported_pagination_import(
    no_pagination_fixture_path: Path, tmp_path: Path
) -> None:
    """The strategy-error import is gone when no method can raise it."""
    source = _items_collection_source(no_pagination_fixture_path, tmp_path / "out")

    assert "UnsupportedPaginationError" not in source


def test_collection_source_imports_envelope_helper(
    no_pagination_fixture_path: Path, tmp_path: Path
) -> None:
    """The collection imports `extract_envelope_items` for the single-fetch path."""
    source = _items_collection_source(no_pagination_fixture_path, tmp_path / "out")

    assert "from ..strategies import extract_envelope_items" in source


def test_collection_source_does_not_reference_pagination_strategy(
    no_pagination_fixture_path: Path, tmp_path: Path
) -> None:
    """The non-paginated collection never touches `client.pagination_strategy`."""
    source = _items_collection_source(no_pagination_fixture_path, tmp_path / "out")

    assert "pagination_strategy" not in source
