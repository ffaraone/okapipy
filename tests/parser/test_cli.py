"""Integration tests for the typer CLI: nlp fetch, spec parse with file/URL/output."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from pytest_mock import MockerFixture
from typer.testing import CliRunner

from okapipy.cli import app
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


def test_nlp_fetch_reports_failure(mocker: MockerFixture, tmp_path: Path) -> None:
    """Download errors surface as a non-zero exit and the message goes to stderr."""
    mocker.patch(
        "okapipy.cli.nlp_cmd.fetch_model",
        side_effect=NlpModelMissingError("xx", str(tmp_path)),
    )

    result = runner.invoke(app, ["nlp", "fetch", "xx", "--cache-dir", str(tmp_path)])

    assert result.exit_code == 1


def test_spec_parse_prints_json_to_stdout(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """Without `--output`, the parsed APIModel is printed as JSON on stdout."""
    fake_model = mocker.Mock(name="APIModel")
    fake_model.model_dump_json.return_value = '{"collections": []}'
    mocker.patch("okapipy.cli.spec_cmd.parse", return_value=fake_model)

    result = runner.invoke(app, ["spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"collections": []}


def test_spec_parse_writes_yaml_when_extension_is_yaml(
    mocker: MockerFixture, simple_spec_path: Path, tmp_path: Path
) -> None:
    """A `.yaml` `--output` argument selects the YAML serializer."""
    from okapipy.parser.model import APIModel, Collection

    mocker.patch(
        "okapipy.cli.spec_cmd.parse",
        return_value=APIModel(collections=[Collection(name="Orders", path="/orders")]),
    )
    target = tmp_path / "out.yaml"

    result = runner.invoke(
        app, ["spec", "parse", str(simple_spec_path), "--output", str(target)]
    )

    assert result.exit_code == 0
    assert yaml.safe_load(target.read_text())["collections"][0]["name"] == "Orders"


def test_spec_parse_rejects_unknown_output_extension(
    mocker: MockerFixture, simple_spec_path: Path, tmp_path: Path
) -> None:
    """An unsupported `--output` extension exits with a non-zero code."""
    from okapipy.parser.model import APIModel

    mocker.patch("okapipy.cli.spec_cmd.parse", return_value=APIModel())
    target = tmp_path / "out.xml"

    result = runner.invoke(
        app, ["spec", "parse", str(simple_spec_path), "--output", str(target)]
    )

    assert result.exit_code == 1


def test_spec_parse_reports_parser_error(
    mocker: MockerFixture, simple_spec_path: Path
) -> None:
    """Any ParserError raised by `parse` is rendered to stderr with a non-zero exit."""
    from okapipy.parser.errors import SpecLoadError

    mocker.patch("okapipy.cli.spec_cmd.parse", side_effect=SpecLoadError("boom"))

    result = runner.invoke(app, ["spec", "parse", str(simple_spec_path)])

    assert result.exit_code == 1


def test_spec_parse_passes_url_source_through(
    mocker: MockerFixture, served_fixtures: object
) -> None:
    """An http(s) source argument is forwarded to `parse` verbatim."""
    from pytest_httpserver import HTTPServer

    from okapipy.parser.model import APIModel

    assert isinstance(served_fixtures, HTTPServer)
    parse = mocker.patch("okapipy.cli.spec_cmd.parse", return_value=APIModel())
    url = served_fixtures.url_for("/simple.yaml")

    result = runner.invoke(app, ["spec", "parse", url])

    assert result.exit_code == 0
    assert parse.call_args.args[0] == url
