"""Integration tests for the `okapipy spec generate --check` / `--quiet` flags."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from okapipy.cli import app

runner = CliRunner()

FIXTURE_BEFORE = """
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

FIXTURE_AFTER = """
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


@pytest.fixture
def before_spec(tmp_path: Path) -> Path:
    p = tmp_path / "before.yaml"
    p.write_text(FIXTURE_BEFORE, encoding="utf-8")
    return p


@pytest.fixture
def after_spec(tmp_path: Path) -> Path:
    p = tmp_path / "after.yaml"
    p.write_text(FIXTURE_AFTER, encoding="utf-8")
    return p


def _generate_args(spec: Path, output: Path) -> list[str]:
    return [
        "spec",
        "generate",
        str(spec),
        "--output",
        str(output),
        "--package",
        "cli",
        "--client-class",
        "CLI",
    ]


def test_check_passes_on_unchanged_regen(before_spec: Path, tmp_path: Path) -> None:
    """`--check` against an already-generated tree exits 0 with a passed panel."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(before_spec, out))

    result = runner.invoke(app, [*_generate_args(before_spec, out), "--check"])

    assert result.exit_code == 0, result.stderr
    assert "--check passed" in result.stderr


def test_check_fails_when_spec_grows(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """`--check` exits non-zero when a spec change introduces drift."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(before_spec, out))

    result = runner.invoke(app, [*_generate_args(after_spec, out), "--check"])

    assert result.exit_code == 1
    assert "--check failed" in result.stderr
    assert "__products_factory__" in result.stderr


def test_check_does_not_modify_disk(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """`--check` is a dry-run — no new files written, no stale files pruned."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(before_spec, out))
    products_user_stub = out / "src" / "cli" / "collections" / "products.py"
    assert not products_user_stub.exists()

    runner.invoke(app, [*_generate_args(after_spec, out), "--check"])

    assert not products_user_stub.exists()


def test_quiet_suppresses_drift_warnings(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """`--quiet` mutes drift warnings (still writes/prunes normally)."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(before_spec, out))

    result = runner.invoke(app, [*_generate_args(after_spec, out), "--quiet"])

    assert result.exit_code == 0
    assert "WARNING" not in result.stderr
    # New stub still created.
    assert (out / "src" / "cli" / "collections" / "products.py").exists()


def test_warnings_visible_without_quiet(
    before_spec: Path, after_spec: Path, tmp_path: Path
) -> None:
    """Default behavior: drift warnings are printed on stderr."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(before_spec, out))

    result = runner.invoke(app, _generate_args(after_spec, out))

    assert result.exit_code == 0
    assert "WARNING" in result.stderr


def test_no_models_flag_skips_models_file(before_spec: Path, tmp_path: Path) -> None:
    """`--no-models` produces a project tree with no `base/models.py` on disk.

    Drives the same code path as `--without-models`. The collections file
    that would otherwise import from `..models` is still emitted but free of
    the import, since the walker sees an empty available-models set.
    """
    out = tmp_path / "out"

    result = runner.invoke(app, [*_generate_args(before_spec, out), "--no-models"])

    assert result.exit_code == 0, result.output
    assert not (out / "src" / "cli" / "base" / "models.py").exists()
    orders = (out / "src" / "cli" / "base" / "collections" / "orders.py").read_text(
        encoding="utf-8"
    )
    assert "from ..models import" not in orders


def test_without_models_alias_works(before_spec: Path, tmp_path: Path) -> None:
    """`--without-models` is accepted as an alias for `--no-models`."""
    out = tmp_path / "out"

    result = runner.invoke(app, [*_generate_args(before_spec, out), "--without-models"])

    assert result.exit_code == 0, result.output
    assert not (out / "src" / "cli" / "base" / "models.py").exists()
