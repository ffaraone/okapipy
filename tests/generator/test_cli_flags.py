"""Integration tests for the `okapipy spec generate --check` / `--quiet` flags."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from okapipy.cli import app

runner = CliRunner()


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


def test_check_passes_on_unchanged_regen(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """`--check` against an already-generated tree exits 0 with a passed panel."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(orders_only_spec_file, out))

    result = runner.invoke(
        app, [*_generate_args(orders_only_spec_file, out), "--check"]
    )

    assert result.exit_code == 0, result.stderr
    assert "--check passed" in result.stderr


def test_check_fails_when_spec_grows(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """`--check` exits non-zero when a spec change introduces drift."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(orders_only_spec_file, out))

    result = runner.invoke(
        app, [*_generate_args(orders_and_products_spec_file, out), "--check"]
    )

    assert result.exit_code == 1
    assert "--check failed" in result.stderr
    assert "__products_factory__" in result.stderr


def test_check_does_not_modify_disk(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """`--check` is a dry-run — no new files written, no stale files pruned."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(orders_only_spec_file, out))
    products_user_stub = out / "src" / "cli" / "collections" / "products.py"
    assert not products_user_stub.exists()

    runner.invoke(app, [*_generate_args(orders_and_products_spec_file, out), "--check"])

    assert not products_user_stub.exists()


def test_quiet_suppresses_drift_warnings(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """`--quiet` mutes drift warnings (still writes/prunes normally)."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(orders_only_spec_file, out))

    result = runner.invoke(
        app, [*_generate_args(orders_and_products_spec_file, out), "--quiet"]
    )

    assert result.exit_code == 0
    assert "WARNING" not in result.stderr
    # New stub still created.
    assert (out / "src" / "cli" / "collections" / "products.py").exists()


def test_warnings_visible_without_quiet(
    orders_only_spec_file: Path, orders_and_products_spec_file: Path, tmp_path: Path
) -> None:
    """Default behavior: drift warnings are printed on stderr."""
    out = tmp_path / "out"
    runner.invoke(app, _generate_args(orders_only_spec_file, out))

    result = runner.invoke(app, _generate_args(orders_and_products_spec_file, out))

    assert result.exit_code == 0
    assert "WARNING" in result.stderr


def test_shape_dicts_skips_models_file(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """`--shape dicts` produces a project tree with no `base/models.py` on disk.

    The collections file that would otherwise import from `..models` is still
    emitted but free of the import, since the walker sees an empty
    available-models set. The client base must also drop the runtime shape
    switch (`shape=` constructor option, `with_shape()`).
    """
    out = tmp_path / "out"

    result = runner.invoke(
        app, [*_generate_args(orders_only_spec_file, out), "--shape", "dicts"]
    )

    assert result.exit_code == 0, result.output
    assert not (out / "src" / "cli" / "base" / "models.py").exists()
    orders = (out / "src" / "cli" / "base" / "collections" / "orders.py").read_text(
        encoding="utf-8"
    )
    assert "from ..models import" not in orders
    client_src = (out / "src" / "cli" / "base" / "client.py").read_text(
        encoding="utf-8"
    )
    assert "with_shape" not in client_src
    assert 'shape: Literal["models", "dicts"]' not in client_src


def test_shape_models_keeps_models_file_and_drops_runtime_switch(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """`--shape models` keeps `base/models.py` but drops the runtime shape switch.

    Bodies / returns are typed as the recovered Pydantic models without the
    `dict[str, Any]` arm; the constructor `shape=` option and `with_shape()`
    method are absent.
    """
    out = tmp_path / "out"

    result = runner.invoke(
        app, [*_generate_args(orders_only_spec_file, out), "--shape", "models"]
    )

    assert result.exit_code == 0, result.output
    assert (out / "src" / "cli" / "base" / "models.py").exists()
    client_src = (out / "src" / "cli" / "base" / "client.py").read_text(
        encoding="utf-8"
    )
    assert "with_shape" not in client_src
    assert 'shape: Literal["models", "dicts"]' not in client_src
