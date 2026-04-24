"""Typer-based CLI surface for okapipy."""

from __future__ import annotations

import typer

from okapipy.cli import nlp_cmd, spec_cmd

app = typer.Typer(no_args_is_help=True, help="okapipy — Python OpenAPI client generator.")
app.add_typer(nlp_cmd.app, name="nlp")
app.add_typer(spec_cmd.app, name="spec")
