"""End-to-end CLI generation against fixtures.

Each test parses a fixture, runs `generate(...)`, writes to a temp directory,
then shells out `uv sync && uv run ruff check . && uv run mypy src`. The
goal is a black-box smoke check that everything wired together produces a
ruff/mypy-clean Python project.

Marked `slow` because `uv sync` provisions a venv per test (~5–10 seconds).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.parser.api import parse

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Helper: run a command, capture, never raise — caller decides on failure."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(
    ("fixture_name", "package", "client_class"),
    [
        ("simple.yaml", "simplecli", "SimpleClient"),
        ("nested.yaml", "nestedcli", "NestedClient"),
        ("pagination.yaml", "pagedcli", "PagedClient"),
    ],
)
def test_generated_tree_passes_lint_and_typecheck(
    tmp_path: Path, fixture_name: str, package: str, client_class: str
) -> None:
    """Every fixture generates a tree that passes `ruff check` and `mypy`.

    We don't run the generated test suite (Phase 9) — only confirm that the
    structural output type-checks and lints, which catches the bulk of template
    regressions.
    """
    fixture = FIXTURES / fixture_name
    api = parse(fixture)
    out = tmp_path / "out"

    vfs = generate(
        api,
        raw_spec=fixture,
        output_dir=out,
        package=package,
        client_class=client_class,
        project_name=f"{package}-test",
    )
    write_to_disk(vfs, out)

    sync = _run(["uv", "sync"], cwd=out)
    assert sync.returncode == 0, f"uv sync failed:\n{sync.stderr}"

    ruff = _run(["uv", "run", "ruff", "check", "."], cwd=out)
    assert ruff.returncode == 0, f"ruff failed:\n{ruff.stdout}\n{ruff.stderr}"

    mypy = _run(["uv", "run", "mypy", "src"], cwd=out)
    assert mypy.returncode == 0, f"mypy failed:\n{mypy.stdout}\n{mypy.stderr}"


def test_cli_invocation_writes_files(tmp_path: Path) -> None:
    """`okapipy spec generate` end-to-end: invoke via `uv run okapipy`, verify files."""
    out = tmp_path / "out"
    cmd = [
        "uv",
        "run",
        "okapipy",
        "spec",
        "generate",
        str(FIXTURES / "simple.yaml"),
        "--output",
        str(out),
        "--package",
        "clitest",
        "--client-class",
        "CLIClient",
    ]
    project_root = Path(__file__).resolve().parents[2]
    result = _run(cmd, cwd=project_root)

    assert result.returncode == 0, f"CLI failed:\n{result.stderr}"
    assert (out / "pyproject.toml").exists()
    assert (out / "src" / "clitest" / "__init__.py").exists()
    assert (out / "src" / "clitest" / "base" / "models.py").exists()
    assert (out / "src" / "clitest" / "base" / "collections" / "orders.py").exists()
