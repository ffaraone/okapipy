"""License and author wiring in the generated project skeleton."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from okapipy.cli import app
from okapipy.generator import branding, generate_for_mount
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.model import APIModel

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "simple.yaml"


def _generate(
    *,
    license_id: str = "Proprietary",
    author: str | None = None,
    project_description: str | None = None,
    repo_url: str | None = None,
) -> dict[str, GeneratedFile]:
    """Render the skeleton with the given license and author, in dicts shape.

    `shape="dicts"` skips the `datamodel-code-generator` step so these tests
    stay fast — they only inspect `LICENSE` and `pyproject.toml`.
    """
    return generate_for_mount(
        APIModel(),
        raw_spec=FIXTURE,
        output_dir=Path("/tmp"),
        package="acme.client",
        client_class="AcmeClient",
        license=license_id,
        author=author,
        project_description=project_description,
        repo_url=repo_url,
        shape="dicts",
    )


@pytest.mark.parametrize(
    ("license_id", "title"),
    [
        ("MIT", "MIT License"),
        ("Apache-2.0", "Apache License"),
        ("BSD-3-Clause", "BSD 3-Clause License"),
        ("BSD-2-Clause", "BSD 2-Clause License"),
        ("MPL-2.0", "Mozilla Public License Version 2.0"),
    ],
)
def test_known_license_emits_titled_text(license_id: str, title: str) -> None:
    """The LICENSE branch for each known SPDX id starts with its canonical title."""
    vfs = _generate(license_id=license_id)
    assert title in vfs["LICENSE"].content


def test_apache_2_0_emits_full_license_body() -> None:
    """The Apache-2.0 branch emits the full license text, not a stub link."""
    body = _generate(license_id="Apache-2.0")["LICENSE"].content
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in body
    assert "Grant of Patent License" in body
    assert "APPENDIX: How to apply the Apache License to your work." in body


def test_bsd_3_clause_emits_third_clause() -> None:
    """The BSD-3-Clause branch contains the non-endorsement
    clause that distinguishes it from BSD-2."""
    body = _generate(license_id="BSD-3-Clause")["LICENSE"].content
    assert "Neither the name of the copyright holder" in body


def test_bsd_2_clause_omits_third_clause() -> None:
    """The BSD-2-Clause branch lacks the non-endorsement clause."""
    body = _generate(license_id="BSD-2-Clause")["LICENSE"].content
    assert "Neither the name of the copyright holder" not in body


def test_mpl_2_0_emits_full_license_body() -> None:
    """The MPL-2.0 branch emits the full Mozilla Public License text, not a stub link."""
    body = _generate(license_id="MPL-2.0")["LICENSE"].content
    assert "Exhibit A - Source Code Form License Notice" in body
    assert "Mozilla Foundation is the license steward" in body


def test_unknown_license_emits_todo_placeholder() -> None:
    """Licenses outside the recognised set fall back to the TODO placeholder branch."""
    body = _generate(license_id="Proprietary")["LICENSE"].content
    assert 'TODO: replace with the full license text for "Proprietary".' in body


def test_license_copyright_includes_current_year() -> None:
    """The copyright line uses the current calendar year."""
    body = _generate(license_id="MIT")["LICENSE"].content
    assert f"Copyright (c) {date.today().year}" in body


def test_license_copyright_falls_back_to_project_name() -> None:
    """When `author` is omitted, the copyright holder defaults to the project name."""
    body = _generate(license_id="MIT")["LICENSE"].content
    assert "Copyright (c) " in body
    assert "client" in body  # project_name defaults to last segment of `acme.client`


def test_license_copyright_uses_author_when_provided() -> None:
    """When `author` is set, the copyright holder is the author, not the project name."""
    body = _generate(license_id="MIT", author="Alice Example")["LICENSE"].content
    assert f"Copyright (c) {date.today().year} Alice Example" in body


def test_pyproject_emits_license_field_for_spdx_id() -> None:
    """The PEP 621 `license` field carries a recognised SPDX identifier verbatim."""
    pyproject = _generate(license_id="Apache-2.0")["pyproject.toml"].content
    assert 'license = "Apache-2.0"' in pyproject


def test_pyproject_omits_license_field_for_unrecognised_id() -> None:
    """Free-form license strings (e.g. `Proprietary`) are not emitted as a `license` field.

    `hatchling` validates the field as an SPDX expression and would refuse the
    project at build time; omitting it keeps `uv sync` working out of the box.
    """
    pyproject = _generate(license_id="Proprietary")["pyproject.toml"].content
    assert "license =" not in pyproject


def test_pyproject_omits_authors_when_no_author() -> None:
    """Without `--author`, `pyproject.toml` does not emit a PEP 621 authors block."""
    pyproject = _generate()["pyproject.toml"].content
    assert "authors" not in pyproject


def test_pyproject_emits_authors_when_author_set() -> None:
    """With `author=...`, `pyproject.toml` emits a PEP 621 `authors` array entry."""
    pyproject = _generate(author="Alice Example")["pyproject.toml"].content
    assert "authors = [" in pyproject
    assert '{ name = "Alice Example" }' in pyproject


def test_pyproject_emits_readme_pointing_at_generated_readme() -> None:
    """`pyproject.toml` always points `readme` at the co-emitted README.md."""
    pyproject = _generate()["pyproject.toml"].content
    assert 'readme = "README.md"' in pyproject


def test_pyproject_always_emits_license_files_glob() -> None:
    """`license-files` is always emitted; the LICENSE file is always generated."""
    pyproject = _generate(license_id="Proprietary")["pyproject.toml"].content
    assert 'license-files = ["LICEN[CS]E*"]' in pyproject


def test_pyproject_emits_license_files_alongside_spdx_license() -> None:
    """`license-files` is emitted regardless of whether the SPDX `license` line is."""
    pyproject = _generate(license_id="MIT")["pyproject.toml"].content
    assert 'license = "MIT"' in pyproject
    assert 'license-files = ["LICEN[CS]E*"]' in pyproject


def test_pyproject_description_defaults_to_generated_blurb() -> None:
    """Without a manifest `project_description`, the description falls back to a generated blurb."""
    pyproject = _generate()["pyproject.toml"].content
    assert 'description = "Generated client for client"' in pyproject


def test_pyproject_description_uses_manifest_value_when_set() -> None:
    """A manifest-supplied `project_description` is emitted verbatim."""
    pyproject = _generate(project_description="Acme commerce SDK")[
        "pyproject.toml"
    ].content
    assert 'description = "Acme commerce SDK"' in pyproject


def test_pyproject_omits_project_urls_when_no_repo_url() -> None:
    """Without `repo_url`, no `[project.urls]` table is emitted."""
    pyproject = _generate()["pyproject.toml"].content
    assert "[project.urls]" not in pyproject


def test_pyproject_emits_github_urls_for_github_repo() -> None:
    """A `github.com` `repo_url` renders Homepage / Repository / Issues entries."""
    pyproject = _generate(repo_url="https://github.com/acme/client")[
        "pyproject.toml"
    ].content
    assert "[project.urls]" in pyproject
    assert 'Homepage = "https://github.com/acme/client"' in pyproject
    assert 'Repository = "https://github.com/acme/client"' in pyproject
    assert 'Issues = "https://github.com/acme/client/issues"' in pyproject


def test_pyproject_omits_issues_for_non_github_repo() -> None:
    """A non-GitHub `repo_url` does not gain a synthetic `/issues` URL."""
    pyproject = _generate(repo_url="https://gitlab.com/acme/client")[
        "pyproject.toml"
    ].content
    assert 'Homepage = "https://gitlab.com/acme/client"' in pyproject
    assert 'Repository = "https://gitlab.com/acme/client"' in pyproject
    assert "Issues" not in pyproject


def test_pyproject_strips_trailing_slash_from_repo_url() -> None:
    """A trailing slash on `repo_url` is normalized away before rendering."""
    pyproject = _generate(repo_url="https://github.com/acme/client/")[
        "pyproject.toml"
    ].content
    assert 'Homepage = "https://github.com/acme/client"' in pyproject
    assert 'Issues = "https://github.com/acme/client/issues"' in pyproject


def test_readme_emits_shields_io_badge_with_brand_color() -> None:
    """The README header carries a shields.io static badge stamped with the okapipy brand color."""
    readme = _generate()["README.md"].content
    expected_badge_src = (
        f"https://img.shields.io/badge/generated_with-okapipy-{branding.OKAPIPY_BRAND_COLOR}"
        f"?logo={branding.OKAPIPY_LOGO_DATA_URI}"
    )
    assert expected_badge_src in readme


def test_readme_badge_omits_label_color_override() -> None:
    """The header badge does not pin a labelColor, so shields.io renders the default gray label."""
    readme = _generate()["README.md"].content
    assert "labelColor=" not in readme


def test_readme_header_badge_links_to_okapipy_repo() -> None:
    """The header badge wraps a link back to the okapipy GitHub repository."""
    readme = _generate()["README.md"].content
    assert f"]({branding.OKAPIPY_REPO_URL})" in readme


def test_readme_footer_embeds_okapipy_badge_image() -> None:
    """The README footer renders the okapipy badge.png hosted on the okapipy repo."""
    readme = _generate()["README.md"].content
    assert branding.OKAPIPY_FOOTER_BADGE_URL in readme
    assert 'alt="generated with okapipy"' in readme


def test_readme_footer_links_to_okapipy_repo() -> None:
    """The footer image is wrapped in an anchor pointing at the okapipy GitHub repository."""
    readme = _generate()["README.md"].content
    expected_anchor = f'<a href="{branding.OKAPIPY_REPO_URL}">'
    assert expected_anchor in readme


def test_cli_author_flag_propagates_to_license_and_pyproject(tmp_path: Path) -> None:
    """The manifest's `author` and `license` fields propagate to LICENSE and pyproject.toml."""
    import yaml

    out = tmp_path / "out"
    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package": "acme.client",
                "client_class": "AcmeClient",
                "license": "MIT",
                "author": "Alice Example",
                "output": str(out),
                "specs": [{"namespace": "", "source": str(FIXTURE)}],
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["generate", "--manifest", str(manifest_path)],
    )

    assert result.exit_code == 0, result.stderr
    license_text = (out / "LICENSE").read_text()
    pyproject_text = (out / "pyproject.toml").read_text()
    assert "Alice Example" in license_text
    assert '{ name = "Alice Example" }' in pyproject_text
