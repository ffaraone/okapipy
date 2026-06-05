"""Schema validation rules for the project manifest.

Covers the cross-field invariants `load_manifest` enforces beyond the
plain-field types: mount-namespace collisions, URL-rejection on `rules`,
and the empty-mount semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from okapipy.generator.errors import ManifestFormatError
from okapipy.manifest import load_manifest


def test_two_specs_with_same_mount_collide(
    write_manifest: Callable[..., Path],
) -> None:
    """Two `specs[]` entries cannot share the same fully-qualified namespace."""
    path = write_manifest(
        overrides={
            "specs": [
                {"namespace": "users", "source": "a.yaml"},
                {"namespace": "users", "source": "b.yaml"},
            ]
        }
    )

    with pytest.raises(ManifestFormatError, match="collision"):
        load_manifest(path)


def test_two_specs_with_same_root_mount_collide(
    write_manifest: Callable[..., Path],
) -> None:
    """Two specs both mounting at the root (empty namespace) collide."""
    path = write_manifest(
        overrides={
            "specs": [
                {"namespace": "", "source": "a.yaml"},
                {"namespace": "", "source": "b.yaml"},
            ]
        }
    )

    with pytest.raises(ManifestFormatError, match="<root>"):
        load_manifest(path)


def test_dotted_mounts_with_shared_parent_do_not_collide(
    write_manifest: Callable[..., Path],
) -> None:
    """`platform.users` and `platform.billing` share a parent but mount distinctly."""
    path = write_manifest(
        overrides={
            "specs": [
                {"namespace": "platform.users", "source": "u.yaml"},
                {"namespace": "platform.billing", "source": "b.yaml"},
            ]
        }
    )

    manifest = load_manifest(path)

    namespaces = [entry.namespace for entry in manifest.specs]
    assert namespaces == ["platform.users", "platform.billing"]


def test_rules_field_rejects_http_url(
    write_manifest: Callable[..., Path],
) -> None:
    """`rules` accepts a local path only; URLs are rejected at load time."""
    path = write_manifest(
        spec_overrides={"rules": "https://example.com/rules.yml"},
    )

    with pytest.raises(ManifestFormatError, match="URL"):
        load_manifest(path)


def test_empty_namespace_is_valid(
    write_manifest: Callable[..., Path],
) -> None:
    """A single spec with `namespace: ''` mounts at the root and is valid."""
    path = write_manifest(spec_overrides={"namespace": ""})

    manifest = load_manifest(path)

    assert manifest.specs[0].namespace == ""


def test_shape_is_restricted_to_known_values(
    write_manifest: Callable[..., Path],
) -> None:
    """Only `auto` / `models` / `dicts` are accepted for `shape`."""
    path = write_manifest(overrides={"shape": "wide"})

    with pytest.raises(ManifestFormatError):
        load_manifest(path)
