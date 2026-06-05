"""Unit tests for `okapipy.generator.compose`.

Compose owns the small data structures the emit loop iterates over —
``MountedSpec``, ``mount_segments``, ``mount_relpath``,
``check_mount_collisions``, ``iter_mount_namespace_prefixes``. The tests
exercise the public surface without touching the parser or the emit
pipeline.
"""

from __future__ import annotations

import pytest

from okapipy.generator.compose import (
    MountedSpec,
    check_mount_collisions,
    iter_mount_namespace_prefixes,
    mount_relpath,
    mount_segments,
)
from okapipy.generator.errors import GenerationError
from okapipy.parser.model import APIModel


def _make_mount(namespace: str, *, index: int = 0) -> MountedSpec:
    """Factory for a `MountedSpec` with an empty APIModel and stub raw spec."""
    return MountedSpec(
        mount_path=mount_segments(namespace),
        api=APIModel(),
        raw_spec={"openapi": "3.0.0"},
        spec_index=index,
    )


def test_mount_segments_root_returns_empty_tuple() -> None:
    """An empty string represents the root mount and produces no segments."""
    assert mount_segments("") == ()


def test_mount_segments_splits_dotted_namespace() -> None:
    """A dotted namespace is split into its component segments."""
    assert mount_segments("platform.users") == ("platform", "users")


def test_mount_segments_strips_leading_and_trailing_dots() -> None:
    """Stray dots at the edges don't produce empty segments."""
    assert mount_segments(".users.") == ("users",)


def test_mount_segments_treats_whitespace_only_as_root() -> None:
    """Pure-whitespace namespaces collapse to the root mount."""
    assert mount_segments("   ") == ()


def test_mount_relpath_root_is_empty_string() -> None:
    """The root mount yields an empty string so callers can concatenate freely."""
    assert mount_relpath(()) == ""


def test_mount_relpath_single_segment_has_trailing_slash() -> None:
    """One segment renders as ``segment/`` so concatenation produces valid paths."""
    assert mount_relpath(("users",)) == "users/"


def test_mount_relpath_dotted_path_joins_with_slashes() -> None:
    """Dotted mounts become slash-joined POSIX paths."""
    assert mount_relpath(("platform", "users")) == "platform/users/"


def test_check_mount_collisions_passes_for_distinct_mounts() -> None:
    """Disjoint mounts raise nothing."""
    mounts = [
        _make_mount("users", index=0),
        _make_mount("billing", index=1),
        _make_mount("platform.audit", index=2),
    ]

    check_mount_collisions(mounts)


def test_check_mount_collisions_raises_on_duplicate_mounts() -> None:
    """Two entries sharing a fully-qualified mount path are rejected."""
    mounts = [
        _make_mount("users", index=0),
        _make_mount("users", index=1),
    ]

    with pytest.raises(GenerationError, match="collision"):
        check_mount_collisions(mounts)


def test_check_mount_collisions_uses_root_label_for_empty_mount() -> None:
    """The error message labels the root mount as ``<root>`` for readability."""
    mounts = [
        _make_mount("", index=0),
        _make_mount("", index=1),
    ]

    with pytest.raises(GenerationError, match="<root>"):
        check_mount_collisions(mounts)


def test_iter_mount_namespace_prefixes_returns_only_non_empty() -> None:
    """The root mount contributes no prefix; non-root mounts contribute every depth."""
    mounts = [
        _make_mount("", index=0),
        _make_mount("users", index=1),
        _make_mount("platform.billing", index=2),
    ]

    prefixes = iter_mount_namespace_prefixes(mounts)

    assert prefixes == [
        ("platform",),
        ("platform", "billing"),
        ("users",),
    ]


def test_iter_mount_namespace_prefixes_deduplicates_shared_parents() -> None:
    """`platform.users` and `platform.billing` share the `platform` prefix once."""
    mounts = [
        _make_mount("platform.users", index=0),
        _make_mount("platform.billing", index=1),
    ]

    prefixes = iter_mount_namespace_prefixes(mounts)

    assert prefixes == [
        ("platform",),
        ("platform", "billing"),
        ("platform", "users"),
    ]
