"""Integration tests for the `okapipy spec generate --check` / `--quiet` flags."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from okapipy.cli import app

runner = CliRunner()

NOISY_SPEC = """
openapi: 3.0.0
info: {title: Noisy, version: 1.0.0}
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
    put:
      summary: Bare PUT — no canonical slot, no x-okapipy-kind override.
      responses:
        '200': {description: OK}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
"""


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


def _flatten_panel(rendered: str) -> str:
    """Strip rich panel borders and reflow wrapped lines into a single string.

    Rich wraps long summary lines and prefixes each line with a `│` border, so
    `"... 1 warning emitted"` may be split across two visual rows. Tests assert
    against the flattened form so wrap points (which depend on terminal width
    and on the temp-dir path length) don't make the assertions flaky.
    """
    return " ".join(rendered.replace("│", " ").split())


def test_summary_panel_reports_warning_count_when_warnings_emitted(
    tmp_path: Path,
) -> None:
    """The final summary panel includes a `N warning(s) emitted` tail when warnings fire.

    A PUT on a bare collection has no canonical slot and is dropped by the parser
    with a warning. The CLI surfaces a single run-wide tally so users notice
    parser-level skips that may have scrolled past in the terminal.
    """
    spec = tmp_path / "noisy.yaml"
    spec.write_text(NOISY_SPEC, encoding="utf-8")
    out = tmp_path / "out"

    result = runner.invoke(app, _generate_args(spec, out))

    assert result.exit_code == 0, result.stderr
    assert "1 warning emitted" in _flatten_panel(result.stderr)


def test_summary_panel_omits_warning_count_when_no_warnings(
    orders_only_spec_file: Path, tmp_path: Path
) -> None:
    """A clean run renders the summary without any `warning(s) emitted` tail."""
    out = tmp_path / "out"

    result = runner.invoke(app, _generate_args(orders_only_spec_file, out))

    assert result.exit_code == 0, result.stderr
    flat = _flatten_panel(result.stderr)
    assert "warning emitted" not in flat
    assert "warnings emitted" not in flat


def test_unmatched_flag_emits_synthetic_namespace_module(tmp_path: Path) -> None:
    """`--unmatched ops` materializes a top-level `ops` namespace and emits its module.

    The NOISY_SPEC fixture has a `PUT /orders` that is normally dropped by
    the routing table. With `--unmatched ops` that op becomes a flat
    action under `ops`, and the generator writes the corresponding
    base-layer module.
    """
    spec = tmp_path / "noisy.yaml"
    spec.write_text(NOISY_SPEC, encoding="utf-8")
    out = tmp_path / "out"

    result = runner.invoke(app, [*_generate_args(spec, out), "--unmatched", "ops"])

    assert result.exit_code == 0, result.stderr
    assert (out / "src" / "cli" / "base" / "namespaces" / "ops.py").exists()
    actions_dir = out / "src" / "cli" / "base" / "actions"
    assert any(actions_dir.iterdir()), "expected at least one synthetic action module"


def test_unmatched_flag_aborts_on_collision(tmp_path: Path) -> None:
    """`--unmatched <name>` exits non-zero when the name collides with a top-level node.

    `NOISY_SPEC` exposes an `/orders` collection; asking for `--unmatched
    orders` must surface `UnmatchedNamespaceCollisionError` and not write
    any output.
    """
    spec = tmp_path / "noisy.yaml"
    spec.write_text(NOISY_SPEC, encoding="utf-8")
    out = tmp_path / "out"

    result = runner.invoke(app, [*_generate_args(spec, out), "--unmatched", "orders"])

    assert result.exit_code == 1
    assert "UnmatchedNamespaceCollisionError" in result.stderr
    assert not out.exists()
