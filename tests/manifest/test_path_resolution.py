"""Relative path resolution: manifest fields anchor against the manifest's parent dir."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from okapipy.manifest import load_manifest


def test_spec_source_resolves_against_manifest_parent(
    write_manifest: Callable[..., Path],
) -> None:
    """`source: foo.yaml` resolves to `<manifest_parent>/foo.yaml`."""
    path = write_manifest(spec_overrides={"source": "specs/api.yaml"})

    manifest = load_manifest(path)

    expected = (path.parent / "specs" / "api.yaml").resolve()
    assert manifest.specs[0].source == str(expected)


def test_spec_source_url_is_passed_through(
    write_manifest: Callable[..., Path],
) -> None:
    """A `source` that is a URL is left untouched by path resolution."""
    url = "https://example.com/openapi.json"
    path = write_manifest(spec_overrides={"source": url})

    manifest = load_manifest(path)

    assert manifest.specs[0].source == url


def test_rules_path_resolves_against_manifest_parent(
    write_manifest: Callable[..., Path],
) -> None:
    """`rules: rules.yml` resolves to `<manifest_parent>/rules.yml`."""
    path = write_manifest(spec_overrides={"rules": "rules.yml"})

    manifest = load_manifest(path)

    assert manifest.specs[0].rules is not None
    assert manifest.specs[0].rules == (path.parent / "rules.yml").resolve()


def test_top_level_paths_resolve_against_manifest_parent(
    write_manifest: Callable[..., Path],
) -> None:
    """`templates_dir`, `model_templates_dir`, `nlp_cache_dir`, `output` all anchor."""
    path = write_manifest(
        overrides={
            "templates_dir": "tpl",
            "model_templates_dir": "model_tpl",
            "nlp_cache_dir": ".cache",
            "output": "out",
        }
    )

    manifest = load_manifest(path)

    assert manifest.templates_dir == (path.parent / "tpl").resolve()
    assert manifest.model_templates_dir == (path.parent / "model_tpl").resolve()
    assert manifest.nlp_cache_dir == (path.parent / ".cache").resolve()
    assert manifest.output == (path.parent / "out").resolve()


def test_absolute_paths_pass_through_unchanged(
    write_manifest: Callable[..., Path],
    tmp_path: Path,
) -> None:
    """An absolute path on a manifest field is returned as-is, not re-anchored."""
    absolute_rules = (tmp_path / "elsewhere" / "rules.yml").resolve()
    path = write_manifest(spec_overrides={"rules": str(absolute_rules)})

    manifest = load_manifest(path)

    assert manifest.specs[0].rules == absolute_rules
