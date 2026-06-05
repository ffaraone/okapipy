"""CLI override merging via `okapipy.manifest.apply_cli_overrides`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from okapipy.manifest import apply_cli_overrides, load_manifest


def test_output_override_replaces_manifest_value(
    write_manifest: Callable[..., Path],
    tmp_path: Path,
) -> None:
    """`--output ./elsewhere` replaces the manifest's `output` field."""
    path = write_manifest(overrides={"output": "from-manifest"})
    manifest = load_manifest(path)
    override = tmp_path / "from-cli"

    merged = apply_cli_overrides(manifest, output=override)

    assert merged.output == override.resolve()


def test_output_override_fills_when_manifest_omits_it(
    write_manifest: Callable[..., Path],
    tmp_path: Path,
) -> None:
    """A manifest with no `output` accepts a CLI override transparently."""
    path = write_manifest()
    manifest = load_manifest(path)
    assert manifest.output is None
    override = tmp_path / "out"

    merged = apply_cli_overrides(manifest, output=override)

    assert merged.output == override.resolve()


def test_no_overrides_returns_same_instance(
    write_manifest: Callable[..., Path],
) -> None:
    """When no override is supplied, the function short-circuits without copying."""
    path = write_manifest()
    manifest = load_manifest(path)

    merged = apply_cli_overrides(manifest)

    assert merged is manifest
