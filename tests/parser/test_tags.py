"""Parser behavior for OpenAPI root `tags[]` → namespace description merging."""

from __future__ import annotations

from pathlib import Path

from okapipy.parser.api import parse


def test_tag_description_lands_on_matching_namespace(tags_spec_path: Path) -> None:
    """A root `tags[]` entry copies its description onto the namespace of the same name."""
    api = parse(tags_spec_path)

    admin = next(ns for ns in api.namespaces if ns.name == "admin")

    assert admin.description == "Administrative endpoints (users, audit, billing)."


def test_namespace_without_matching_tag_keeps_none(tags_spec_path: Path) -> None:
    """A namespace with no matching tag entry keeps `description = None`."""
    api = parse(tags_spec_path)

    public = next(ns for ns in api.namespaces if ns.name == "public")

    assert public.description is None


def test_orphan_tag_does_not_create_a_namespace(tags_spec_path: Path) -> None:
    """A root `tags[]` entry with no matching path does not synthesize a namespace."""
    api = parse(tags_spec_path)

    assert all(ns.name != "orphan" for ns in api.namespaces)


def test_tag_with_blank_description_is_ignored(
    tags_spec_path: Path, tmp_path: Path
) -> None:
    """A `tags[]` entry whose description is blank does not overwrite an existing description."""
    spec = (tags_spec_path).read_text()
    target = tmp_path / "blank.yaml"
    blank_spec = spec.replace(
        "  - name: admin\n    description: Administrative endpoints (users, audit, billing).\n",
        '  - name: admin\n    description: "   "\n',
    )
    target.write_text(blank_spec)

    api = parse(target)
    admin = next(ns for ns in api.namespaces if ns.name == "admin")

    assert admin.description is None
