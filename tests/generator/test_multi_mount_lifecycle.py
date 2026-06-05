"""Multi-mount regeneration, drift detection, and pruning.

These tests write a manifest to disk, generate, write to disk, then
regenerate with a tweaked manifest and verify the round trip:

* the second generation is a no-op when nothing changed (`--check`
  passes),
* adding a new collection to one mount fires a drift warning on the
  *correct* mount's parent stub (not the other mount),
* dropping a `specs[]` entry prunes every base file under that mount,
* user-layer stubs survive every regeneration unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from okapipy.generator import generate
from okapipy.generator.vfs import write_to_disk
from okapipy.manifest import load_manifest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def write_two_mount_setup(
    tmp_path: Path,
) -> Callable[..., tuple[Path, Path, Path]]:
    """Return a factory that drops two specs + a manifest into tmp_path.

    The factory writes:
    * `tmp_path/specs/users.yaml` — a copy of the `simple` fixture.
    * `tmp_path/specs/admin.yaml` — a copy of the `singletons` fixture.
    * `tmp_path/okapipy.yml` — a manifest mounting them under `users` /
      `admin` with the requested shape.

    Returns `(manifest_path, users_spec_path, admin_spec_path)`.
    """

    def _factory(*, shape: str = "dicts") -> tuple[Path, Path, Path]:
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir(exist_ok=True)
        users_spec = specs_dir / "users.yaml"
        admin_spec = specs_dir / "admin.yaml"
        users_spec.write_text(
            (FIXTURES / "simple.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        admin_spec.write_text(
            (FIXTURES / "singletons.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        manifest_path = tmp_path / "okapipy.yml"
        manifest_path.write_text(
            yaml.safe_dump(
                {
                    "package": "acme.multi",
                    "client_class": "MultiClient",
                    "shape": shape,
                    "output": str(tmp_path / "out"),
                    "specs": [
                        {"namespace": "users", "source": str(users_spec)},
                        {"namespace": "admin", "source": str(admin_spec)},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest_path, users_spec, admin_spec

    return _factory


def test_regenerate_is_idempotent(
    write_two_mount_setup: Callable[..., tuple[Path, Path, Path]],
    tmp_path: Path,
) -> None:
    """Running `generate` twice against an unchanged manifest writes the same bytes.

    Excludes `_generated.json`, whose `generated_at` timestamp differs
    by design between runs.
    """
    manifest_path, _, _ = write_two_mount_setup()
    manifest = load_manifest(manifest_path)
    out = tmp_path / "out"

    first = generate(manifest)
    write_to_disk(first, out)
    second = generate(manifest)

    for path, file in first.items():
        if path.endswith("_generated.json"):
            continue
        assert second[path].content == file.content, path


def test_check_passes_on_unchanged_regeneration(
    write_two_mount_setup: Callable[..., tuple[Path, Path, Path]],
    tmp_path: Path,
) -> None:
    """`write_to_disk(dry_run=True)` reports no changes on a regen of the same manifest."""
    manifest_path, _, _ = write_two_mount_setup()
    out = tmp_path / "out"
    write_to_disk(generate(load_manifest(manifest_path)), out)

    report = write_to_disk(generate(load_manifest(manifest_path)), out, dry_run=True)

    assert report.would_change is False
    assert report.warnings == []
    assert report.pruned == []


def test_drift_warning_fires_for_new_child_in_one_mount(
    write_two_mount_setup: Callable[..., tuple[Path, Path, Path]],
    tmp_path: Path,
) -> None:
    """Adding a collection to one spec drifts that mount's parent stub.

    The simple fixture's user-layer mount stub is auto-wired to the
    spec's top-level on first generation; adding a new top-level
    collection later fires a drift warning that names the missing
    `__<new>_factory__` line — and crucially, the warning is rooted at
    `users/__init__.py`, not `admin/__init__.py`.
    """
    manifest_path, users_spec, _ = write_two_mount_setup()
    out = tmp_path / "out"
    write_to_disk(generate(load_manifest(manifest_path)), out)

    # Grow the users spec with a new top-level collection.
    grown_yaml = """
openapi: 3.0.0
info: {title: Grown, version: 1.0.0}
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
  /widgets:
    get:
      summary: List widgets
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Widget'}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
    Widget: {type: object, properties: {id: {type: string}}}
"""
    users_spec.write_text(grown_yaml, encoding="utf-8")

    report = write_to_disk(generate(load_manifest(manifest_path)), out, dry_run=True)

    assert any(
        "__widgets_factory__" in w and "users/__init__.py" in w for w in report.warnings
    ), report.warnings
    # No warning should mention the admin mount.
    assert not any("admin/__init__.py" in w for w in report.warnings), report.warnings


def test_removing_a_specs_entry_prunes_its_base_subtree(
    write_two_mount_setup: Callable[..., tuple[Path, Path, Path]],
    tmp_path: Path,
) -> None:
    """Dropping the admin spec from the manifest deletes every `base/admin/*` file."""
    manifest_path, _, _ = write_two_mount_setup()
    out = tmp_path / "out"
    write_to_disk(generate(load_manifest(manifest_path)), out)
    admin_base_dir = out / "src" / "acme" / "multi" / "base" / "admin"
    assert admin_base_dir.exists()

    # Rewrite the manifest with the admin entry removed.
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package": "acme.multi",
                "client_class": "MultiClient",
                "shape": "dicts",
                "output": str(out),
                "specs": [
                    {
                        "namespace": "users",
                        "source": str(tmp_path / "specs/users.yaml"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    report = write_to_disk(generate(load_manifest(manifest_path)), out)

    assert any("base/admin/" in p for p in report.pruned), report.pruned


def test_user_layer_stubs_survive_regeneration_across_mounts(
    write_two_mount_setup: Callable[..., tuple[Path, Path, Path]],
    tmp_path: Path,
) -> None:
    """One-shot user stubs in every mount are never overwritten on regen.

    Mutates the user-layer `users/__init__.py` stub between runs and
    verifies the second generation does not clobber the edit.
    """
    manifest_path, _, _ = write_two_mount_setup()
    out = tmp_path / "out"
    write_to_disk(generate(load_manifest(manifest_path)), out)
    users_init = out / "src" / "acme" / "multi" / "users" / "__init__.py"
    original = users_init.read_text(encoding="utf-8")
    sentinel = original + "\n# custom edit — must survive regeneration\n"
    users_init.write_text(sentinel, encoding="utf-8")

    write_to_disk(generate(load_manifest(manifest_path)), out)

    assert users_init.read_text(encoding="utf-8") == sentinel
