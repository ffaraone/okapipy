"""Tests for the dump module: format inference, JSON output, YAML output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from okapipy.parser.dump import to_json, to_yaml, write
from okapipy.parser.model import APIModel, Collection


def _sample_model() -> APIModel:
    """Return a small APIModel exercised by the dump tests."""
    return APIModel(collections=[Collection(name="Orders", path="/orders")])


def test_to_json_emits_valid_json_with_indentation() -> None:
    """`to_json` returns parseable JSON containing the model's content."""
    text = to_json(_sample_model())

    parsed = json.loads(text)
    assert parsed["collections"][0]["name"] == "Orders"


def test_to_yaml_emits_parseable_yaml() -> None:
    """`to_yaml` round-trips through yaml.safe_load to the same content as the model."""
    text = to_yaml(_sample_model())

    parsed = yaml.safe_load(text)
    assert parsed["collections"][0]["path"] == "/orders"


def test_write_uses_json_for_json_extension(tmp_path: Path) -> None:
    """A `.json` destination triggers the JSON serializer."""
    target = tmp_path / "out.json"

    write(_sample_model(), target)

    assert json.loads(target.read_text())["collections"][0]["name"] == "Orders"


def test_write_uses_yaml_for_yaml_extension(tmp_path: Path) -> None:
    """A `.yaml` destination triggers the YAML serializer."""
    target = tmp_path / "out.yaml"

    write(_sample_model(), target)

    parsed = yaml.safe_load(target.read_text())
    assert parsed["collections"][0]["path"] == "/orders"


def test_write_accepts_yml_alias(tmp_path: Path) -> None:
    """`.yml` is treated as YAML, same as `.yaml`."""
    target = tmp_path / "out.yml"

    write(_sample_model(), target)

    assert yaml.safe_load(target.read_text())["collections"][0]["name"] == "Orders"


def test_write_rejects_unknown_extension(tmp_path: Path) -> None:
    """An unsupported suffix raises ValueError naming the supported set."""
    target = tmp_path / "out.xml"

    with pytest.raises(ValueError, match="unsupported"):
        write(_sample_model(), target)
