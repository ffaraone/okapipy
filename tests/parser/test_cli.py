"""Integration tests for the typer CLI: nlp fetch, spec parse, verbosity flags."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from okapipy.cli import app
from okapipy.cli.console import LOGGER_NAME, level_for, setup_logging
from okapipy.parser.errors import NlpModelMissingError

runner = CliRunner()


def test_nlp_fetch_invokes_the_loader(mocker: MockerFixture, tmp_path: Path) -> None:
    """`okapipy nlp fetch en` calls fetch_model with the right cache directory."""
    fetch = mocker.patch(
        "okapipy.cli.nlp_cmd.fetch_model",
        return_value=tmp_path / "en_core_web_sm",
    )

    result = runner.invoke(app, ["nlp", "fetch", "en", "--cache-dir", str(tmp_path)])

    assert result.exit_code == 0
    fetch.assert_called_once_with("en", tmp_path)


def test_nlp_fetch_renders_success_panel(mocker: MockerFixture, tmp_path: Path) -> None:
    """A successful fetch produces a green success panel mentioning the install path."""
    install_dir = tmp_path / "en_core_web_sm"
    mocker.patch("okapipy.cli.nlp_cmd.fetch_model", return_value=install_dir)

    result = runner.invoke(app, ["nlp", "fetch", "en", "--cache-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert "Installed model into" in result.stderr
    assert str(install_dir) in result.stderr


def test_nlp_fetch_reports_failure(mocker: MockerFixture, tmp_path: Path) -> None:
    """Download errors surface as a non-zero exit and the panel goes to stderr."""
    mocker.patch(
        "okapipy.cli.nlp_cmd.fetch_model",
        side_effect=NlpModelMissingError("xx", str(tmp_path)),
    )

    result = runner.invoke(app, ["nlp", "fetch", "xx", "--cache-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "Error" in result.stderr
    assert "NlpModelMissingError" in result.stderr


def test_spec_parse_prints_json_to_stdout(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """Without `--output`, the parsed APIModel is printed as JSON on stdout."""
    from okapipy.parser.model import APIModel, Collection

    mocker.patch(
        "okapipy.cli.spec_cmd._run_pipeline",
        return_value=APIModel(collections=[Collection(name="Orders", path="/orders")]),
    )

    result = runner.invoke(app, ["spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["collections"][0]["name"] == "Orders"


def test_spec_parse_writes_yaml_when_extension_is_yaml(
    mocker: MockerFixture, simple_spec_path: Path, tmp_path: Path
) -> None:
    """A `.yaml` `--output` argument selects the YAML serializer."""
    from okapipy.parser.model import APIModel, Collection

    mocker.patch(
        "okapipy.cli.spec_cmd._run_pipeline",
        return_value=APIModel(collections=[Collection(name="Orders", path="/orders")]),
    )
    target = tmp_path / "out.yaml"

    result = runner.invoke(
        app, ["spec", "parse", str(simple_spec_path), "--output", str(target)]
    )

    assert result.exit_code == 0
    assert yaml.safe_load(target.read_text())["collections"][0]["name"] == "Orders"
    assert "Wrote" in result.stderr


def test_spec_parse_rejects_unknown_output_extension(
    mocker: MockerFixture, simple_spec_path: Path, tmp_path: Path
) -> None:
    """An unsupported `--output` extension exits non-zero with an error panel."""
    from okapipy.parser.model import APIModel

    mocker.patch("okapipy.cli.spec_cmd._run_pipeline", return_value=APIModel())
    target = tmp_path / "out.xml"

    result = runner.invoke(
        app, ["spec", "parse", str(simple_spec_path), "--output", str(target)]
    )

    assert result.exit_code == 1
    assert "Error" in result.stderr


def test_spec_parse_reports_parser_error(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """A ParserError raised during the pipeline is rendered as a red panel."""
    from okapipy.parser.errors import SpecLoadError

    mocker.patch(
        "okapipy.cli.spec_cmd._run_pipeline", side_effect=SpecLoadError("boom")
    )

    result = runner.invoke(app, ["spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 1
    assert "SpecLoadError" in result.stderr
    assert "boom" in result.stderr


def test_spec_parse_passes_url_source_through(
    mocker: MockerFixture, served_fixtures: object
) -> None:
    """An http(s) source argument is forwarded to the loader phase verbatim."""
    from pytest_httpserver import HTTPServer

    from okapipy.parser.model import APIModel

    assert isinstance(served_fixtures, HTTPServer)
    pipeline = mocker.patch(
        "okapipy.cli.spec_cmd._run_pipeline", return_value=APIModel()
    )
    url = served_fixtures.url_for("/simple.yaml")

    result = runner.invoke(app, ["spec", "parse", url])

    assert result.exit_code == 0
    assert pipeline.call_args.kwargs["source"] == url


def test_spec_parse_renders_summary_table(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """The post-parse summary lists counts for each node kind on stderr."""
    from okapipy.parser.model import APIModel, Collection, Namespace, Resource

    api = APIModel(
        collections=[
            Collection(
                name="Orders",
                path="/orders",
                resource=Resource(name="Order", path="/orders/{id}"),
            )
        ],
        namespaces=[Namespace(name="auth")],
    )
    mocker.patch("okapipy.cli.spec_cmd._run_pipeline", return_value=api)

    result = runner.invoke(app, ["spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 0
    assert "Namespaces" in result.stderr
    assert "Collections" in result.stderr
    assert "Resources" in result.stderr
    assert "Actions" in result.stderr


def test_setup_logging_level_mapping() -> None:
    """`level_for` maps verbosity counts to WARNING / INFO / DEBUG."""
    assert level_for(0) == logging.WARNING
    assert level_for(1) == logging.INFO
    assert level_for(2) == logging.DEBUG
    assert level_for(5) == logging.DEBUG


def test_setup_logging_replaces_handlers_each_call() -> None:
    """Repeated `setup_logging` calls keep exactly one user-visible RichHandler.

    The shared `WarningCounter` is registered once and preserved across calls so
    its tally survives, but stale `RichHandler`s from prior invocations are
    discarded; the surviving rich handler reflects the latest verbosity.
    """
    from rich.logging import RichHandler

    setup_logging(0)
    setup_logging(2)
    logger = logging.getLogger(LOGGER_NAME)
    rich_handlers = [h for h in logger.handlers if isinstance(h, RichHandler)]
    assert len(rich_handlers) == 1
    assert rich_handlers[0].level == logging.DEBUG


def test_top_level_verbose_flag_sets_handler_level(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """`-v` raises the rich handler to INFO; `-vv` raises it to DEBUG."""
    from rich.logging import RichHandler

    from okapipy.parser.model import APIModel

    mocker.patch("okapipy.cli.spec_cmd._run_pipeline", return_value=APIModel())

    def rich_handler() -> RichHandler:
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            if isinstance(handler, RichHandler):
                return handler
        raise AssertionError("no RichHandler attached to the okapipy logger")

    runner.invoke(app, ["-v", "spec", "parse", str(simple_spec_path)])
    assert rich_handler().level == logging.INFO

    runner.invoke(app, ["-vv", "spec", "parse", str(simple_spec_path)])
    assert rich_handler().level == logging.DEBUG


def test_default_verbosity_silences_info_logs(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """Without `-v`, INFO-level parser logs are not surfaced to stderr."""
    from okapipy.parser.model import APIModel

    def fake_pipeline(**_: object) -> APIModel:
        logging.getLogger("okapipy.parser.builder").info("excluding /healthz")
        return APIModel()

    mocker.patch("okapipy.cli.spec_cmd._run_pipeline", side_effect=fake_pipeline)

    result = runner.invoke(app, ["spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 0
    assert "excluding /healthz" not in result.stderr


def test_verbose_flag_shows_info_logs(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """With `-v`, parser INFO logs are surfaced to stderr via RichHandler."""
    from okapipy.parser.model import APIModel

    def fake_pipeline(**_: object) -> APIModel:
        logging.getLogger("okapipy.parser.builder").info("excluding /healthz")
        return APIModel()

    mocker.patch("okapipy.cli.spec_cmd._run_pipeline", side_effect=fake_pipeline)

    result = runner.invoke(app, ["-v", "spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 0
    assert "excluding /healthz" in result.stderr
