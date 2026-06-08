"""Tests for `okapipy init` — the starter-manifest scaffolder.

`init` writes a starter `okapipy.yml` next to the consumer's project.
Without a `SOURCE` argument the manifest is intentionally invalid (the
`specs:` list is empty, and Pydantic requires at least one entry), so a
half-configured manifest cannot silently generate against the example
package name.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from okapipy.cli import app
from okapipy.generator.errors import ManifestFormatError
from okapipy.manifest import load_manifest

runner = CliRunner()


def test_init_writes_default_manifest_with_empty_specs(tmp_path: Path) -> None:
    """Without a SOURCE argument, init writes a manifest with no `specs[]` entries."""
    manifest_path = tmp_path / "okapipy.yml"

    result = runner.invoke(app, ["init", "--manifest", str(manifest_path)])

    assert result.exit_code == 0, result.stderr
    assert manifest_path.exists()
    body = manifest_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(body)
    assert payload["package"] == "acme.commerce"
    assert payload["client_class"] == "CommerceClient"
    # `specs:` is present but empty — Pydantic rejects empty lists, which is
    # the safety net against generating against the example package name.
    assert payload["specs"] is None


def test_init_starter_includes_all_project_metadata(tmp_path: Path) -> None:
    """The starter exposes every PEP 621-flavored project metadata field."""
    manifest_path = tmp_path / "okapipy.yml"

    runner.invoke(app, ["init", "--manifest", str(manifest_path)])

    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert payload["project_name"] == "commerce"
    assert payload["project_description"] == "Generated client for commerce"
    assert payload["project_version"] == "0.1.0"
    assert payload["python_version"] == "3.13"
    assert payload["license"] == "Proprietary"
    assert payload["author"] == "Your Organization"
    assert payload["repo_url"] == "https://github.com/your-org/your-repo"


def test_init_default_package_is_valid_lowercase_example(tmp_path: Path) -> None:
    """The default `package` is a valid lowercase Python package path."""
    manifest_path = tmp_path / "okapipy.yml"

    runner.invoke(app, ["init", "--manifest", str(manifest_path)])

    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    pkg: str = payload["package"]
    assert pkg == pkg.lower()
    assert all(segment.isidentifier() for segment in pkg.split("."))


def test_init_derives_project_name_from_supplied_package(tmp_path: Path) -> None:
    """When `--package` is given, `project_name` derives from its last segment."""
    manifest_path = tmp_path / "okapipy.yml"

    runner.invoke(
        app,
        [
            "init",
            "--manifest",
            str(manifest_path),
            "--package",
            "mycorp.sdk",
            "--client-class",
            "SdkClient",
        ],
    )

    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert payload["package"] == "mycorp.sdk"
    assert payload["project_name"] == "sdk"
    assert payload["project_description"] == "Generated client for sdk"


def test_init_with_source_writes_single_spec_entry(tmp_path: Path) -> None:
    """When SOURCE is given, init writes one root-mount spec entry."""
    manifest_path = tmp_path / "okapipy.yml"
    spec_path = tmp_path / "openapi.yaml"

    result = runner.invoke(
        app,
        [
            "init",
            str(spec_path),
            "--manifest",
            str(manifest_path),
            "--package",
            "acme.commerce",
            "--client-class",
            "CommerceClient",
        ],
    )

    assert result.exit_code == 0, result.stderr
    body = manifest_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(body)
    assert payload["package"] == "acme.commerce"
    assert payload["client_class"] == "CommerceClient"
    assert len(payload["specs"]) == 1
    assert payload["specs"][0]["namespace"] == ""
    assert payload["specs"][0]["source"] == str(spec_path)


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """A second run without --force exits non-zero and leaves the file alone."""
    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text("preexisting: true\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--manifest", str(manifest_path)])

    assert result.exit_code == 1
    assert "refusing to overwrite" in result.stderr
    assert manifest_path.read_text(encoding="utf-8") == "preexisting: true\n"


def test_init_overwrites_with_force(tmp_path: Path) -> None:
    """`--force` lets init replace an existing manifest."""
    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text("preexisting: true\n", encoding="utf-8")
    spec_path = tmp_path / "openapi.yaml"

    result = runner.invoke(
        app,
        [
            "init",
            str(spec_path),
            "--manifest",
            str(manifest_path),
            "--package",
            "acme.commerce",
            "--client-class",
            "CommerceClient",
            "--force",
        ],
    )

    assert result.exit_code == 0, result.stderr
    body = manifest_path.read_text(encoding="utf-8")
    assert "preexisting" not in body
    assert "acme.commerce" in body


def test_init_starter_without_package_fails_to_load(tmp_path: Path) -> None:
    """A placeholder manifest cannot be loaded — the user must edit it first."""
    manifest_path = tmp_path / "okapipy.yml"
    runner.invoke(app, ["init", "--manifest", str(manifest_path)])

    try:
        load_manifest(manifest_path)
    except ManifestFormatError:
        pass
    else:
        raise AssertionError(
            "expected starter manifest to fail validation until the user edits it"
        )


def test_init_with_source_loads_after_fill_in(tmp_path: Path) -> None:
    """A starter with all required fields supplied loads successfully."""
    manifest_path = tmp_path / "okapipy.yml"
    spec_path = tmp_path / "openapi.yaml"

    runner.invoke(
        app,
        [
            "init",
            str(spec_path),
            "--manifest",
            str(manifest_path),
            "--package",
            "acme.commerce",
            "--client-class",
            "CommerceClient",
        ],
    )

    manifest = load_manifest(manifest_path)

    assert manifest.package == "acme.commerce"
    assert manifest.specs[0].source == str(spec_path)
