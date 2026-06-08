"""Happy-path loading + error surface for `okapipy.manifest.load_manifest`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from okapipy.generator.errors import ManifestFormatError, ManifestNotFoundError
from okapipy.manifest import GenerationManifest, SpecEntry, load_manifest


def test_load_manifest_reads_a_minimal_yaml(
    write_manifest: Callable[..., Path],
) -> None:
    """A manifest with only the required fields loads and exposes them."""
    path = write_manifest()

    manifest = load_manifest(path)

    assert isinstance(manifest, GenerationManifest)
    assert manifest.package == "acme.commerce"
    assert manifest.client_class == "CommerceClient"
    assert len(manifest.specs) == 1
    assert isinstance(manifest.specs[0], SpecEntry)
    assert manifest.specs[0].namespace == ""


def test_load_manifest_reads_a_json_file(
    write_manifest: Callable[..., Path],
) -> None:
    """Suffix-based format detection: `.json` files are parsed as JSON."""
    path = write_manifest(filename="okapipy.json", format="json")

    manifest = load_manifest(path)

    assert manifest.package == "acme.commerce"


def test_load_manifest_defaults_are_filled_in(
    write_manifest: Callable[..., Path],
) -> None:
    """Optional fields take their declared defaults when absent."""
    path = write_manifest()

    manifest = load_manifest(path)

    assert manifest.shape == "auto"
    assert manifest.lang == "en"
    assert manifest.project_version == "0.1.0"
    assert manifest.python_version == "3.13"
    assert manifest.license == "Proprietary"
    assert manifest.project_description is None
    assert manifest.repo_url is None


def test_load_manifest_carries_project_description_and_repo_url(
    write_manifest: Callable[..., Path],
) -> None:
    """`project_description` and `repo_url` round-trip through the loader."""
    path = write_manifest(
        overrides={
            "project_description": "Acme commerce SDK",
            "repo_url": "https://github.com/acme/client",
        }
    )

    manifest = load_manifest(path)

    assert manifest.project_description == "Acme commerce SDK"
    assert manifest.repo_url == "https://github.com/acme/client"


def test_load_manifest_raises_when_file_missing(tmp_path: Path) -> None:
    """A missing manifest raises `ManifestNotFoundError` with the path."""
    missing = tmp_path / "no-such-file.yml"

    with pytest.raises(ManifestNotFoundError, match=r"no-such-file\.yml"):
        load_manifest(missing)


def test_load_manifest_raises_on_malformed_yaml(tmp_path: Path) -> None:
    """Malformed YAML surfaces as `ManifestFormatError`."""
    path = tmp_path / "okapipy.yml"
    path.write_text("package: acme.commerce\n  bad: : :\n", encoding="utf-8")

    with pytest.raises(ManifestFormatError):
        load_manifest(path)


def test_load_manifest_rejects_top_level_list(tmp_path: Path) -> None:
    """A list at the top level is not a valid manifest."""
    path = tmp_path / "okapipy.yml"
    path.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(ManifestFormatError, match="mapping"):
        load_manifest(path)


def test_load_manifest_requires_package(
    write_manifest: Callable[..., Path],
) -> None:
    """`package` is mandatory; omitting it raises `ManifestFormatError`."""
    path = write_manifest()
    # Rewrite the file without the package field.
    path.write_text(
        "client_class: CommerceClient\nspecs:\n  - namespace: ''\n    source: s.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(ManifestFormatError, match="package"):
        load_manifest(path)


def test_load_manifest_requires_at_least_one_spec(
    write_manifest: Callable[..., Path],
) -> None:
    """An empty `specs[]` is rejected — every project must declare at least one."""
    path = write_manifest(overrides={"specs": []})

    with pytest.raises(ManifestFormatError, match="specs"):
        load_manifest(path)


def test_load_manifest_rejects_unknown_top_level_fields(
    write_manifest: Callable[..., Path],
) -> None:
    """Typos at the top level fail validation rather than silently no-op."""
    path = write_manifest(overrides={"packagex": "acme.misspelled"})

    with pytest.raises(ManifestFormatError):
        load_manifest(path)


def test_load_manifest_rejects_unknown_spec_fields(
    write_manifest: Callable[..., Path],
) -> None:
    """Typos inside a spec entry fail validation rather than silently no-op."""
    path = write_manifest(spec_overrides={"strip_pref": "/v1"})

    with pytest.raises(ManifestFormatError):
        load_manifest(path)
