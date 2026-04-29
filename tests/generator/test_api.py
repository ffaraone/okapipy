"""Smoke tests for the generator entry point."""

from __future__ import annotations

from pathlib import Path

from okapipy.generator import GenerationError, generate
from okapipy.parser.model import APIModel

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "simple.yaml"


def _generate_skeleton(tmp_path: Path) -> dict[str, str]:
    """Helper: render the full skeleton against an empty APIModel + a real spec.

    `raw_spec` must be a real OpenAPI document because Phase 4 invokes
    `datamodel-code-generator` to render `models.py`. The empty `APIModel` keeps
    the namespace/collection/resource/action emitters quiet — Phase 6 covers
    those paths.
    """
    return generate(
        APIModel(),
        raw_spec=FIXTURE,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )


def test_generate_returns_dict() -> None:
    """`generate(...)` returns a `dict[str, str]` populated with the skeleton.

    The contract is documented in §3.1 of `generator_plan.md`: a virtual FS keyed
    on POSIX-style relative paths, values are file contents.
    """
    vfs = _generate_skeleton(Path("/tmp"))

    assert isinstance(vfs, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in vfs.items())


def test_skeleton_emits_expected_paths() -> None:
    """The skeleton contains pyproject, README, LICENSE, gitignore, python-version,
    py.typed, and the package's `__init__.py` / `client.py` / `models.py` stubs.

    Later phases overwrite the stubs; their presence here guarantees the generated
    tree is import-clean from Phase 2 onwards.
    """
    vfs = _generate_skeleton(Path("/tmp"))

    assert "pyproject.toml" in vfs
    assert "README.md" in vfs
    assert "LICENSE" in vfs
    assert ".gitignore" in vfs
    assert ".python-version" in vfs
    assert "src/acme/client/__init__.py" in vfs
    assert "src/acme/client/py.typed" in vfs
    assert "src/acme/client/client.py" in vfs
    assert "src/acme/client/models.py" in vfs


def test_skeleton_substitutes_context_variables() -> None:
    """Templated values flow through: client class, package, project name."""
    vfs = _generate_skeleton(Path("/tmp"))

    pyproject = vfs["pyproject.toml"]
    readme = vfs["README.md"]
    assert 'name = "client"' in pyproject  # project_name defaults to last segment
    assert "AcmeClient" in readme
    assert "acme.client" in readme


def test_generation_error_is_exported() -> None:
    """`GenerationError` is the documented base class for generator failures."""
    assert issubclass(GenerationError, Exception)
