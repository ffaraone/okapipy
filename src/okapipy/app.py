"""Console-script entry point referenced by `[project.scripts]` in pyproject.toml."""

from __future__ import annotations

from okapipy.cli import app


def main() -> None:
    """Invoke the typer app; this is what the `okapipy` console script calls."""
    app()
