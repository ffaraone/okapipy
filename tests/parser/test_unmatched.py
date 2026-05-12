"""Tests for the `--unmatched <namespace>` escape hatch.

Verifies that the parser buffers operations that would otherwise be
dropped by the routing table and synthesizes a top-level Namespace
holding one Action per buffered op when `unmatched_namespace` is set,
plus the collision check that guards against duplicate top-level
identifiers.
"""

from __future__ import annotations

from typing import Any

import pytest
from spacy.language import Language

from okapipy.parser.builder import build
from okapipy.parser.errors import UnmatchedNamespaceCollisionError
from okapipy.parser.model import Action
from okapipy.parser.rules import Rules


def _spec_with_put_on_collection(
    operation_id: str | None = "bulkUpdateUsers",
) -> dict[str, Any]:
    """An OpenAPI spec whose `/users` collection has a PUT — a routing-table miss.

    `_route` drops PUT on a Collection unless it carries an action hint, so
    this spec is the smallest reproducer of the unmatched-op path.
    """
    operation: dict[str, Any] = {"responses": {"200": {"description": "OK"}}}
    if operation_id is not None:
        operation["operationId"] = operation_id
    return {
        "paths": {
            "/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "put": operation,
            }
        }
    }


def test_build_drops_unmatched_op_when_flag_is_off(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """With no `unmatched_namespace`, a PUT on `/users` is logged and dropped."""
    with caplog.at_level("WARNING"):
        api = build(_spec_with_put_on_collection(), Rules(), english_nlp)

    assert api.namespaces == []
    assert "PUT /users" in caplog.text


def test_build_attaches_unmatched_namespace_with_action_per_op(
    english_nlp: Language,
) -> None:
    """A PUT on `/users` lands as an action under the requested namespace."""
    api = build(
        _spec_with_put_on_collection(),
        Rules(),
        english_nlp,
        unmatched_namespace="ops",
    )

    ops = next(ns for ns in api.namespaces if ns.name == "ops")
    assert [act.name for act in ops.actions] == ["BulkUpdateUsers"]
    action = ops.actions[0]
    assert action.path == "/users"
    assert action.attr_override == "bulk_update_users"
    assert [op.method for op in action.operations] == ["PUT"]


def test_build_unmatched_falls_back_to_method_and_path_when_no_operation_id(
    english_nlp: Language,
) -> None:
    """A buffered op with no `operationId` is named `<method>_<sanitized_path>`."""
    api = build(
        _spec_with_put_on_collection(operation_id=None),
        Rules(),
        english_nlp,
        unmatched_namespace="ops",
    )

    ops = next(ns for ns in api.namespaces if ns.name == "ops")
    assert [act.attr_override for act in ops.actions] == ["put_users"]
    assert [act.name for act in ops.actions] == ["PutUsers"]


def test_build_unmatched_disambiguates_duplicate_operation_ids(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """Two unmatched ops sharing an `operationId` get `_N` / `N` suffixes and a warning."""
    spec: dict[str, Any] = {
        "paths": {
            "/users": {
                "put": {
                    "operationId": "bulkUpdate",
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/accounts": {
                "put": {
                    "operationId": "bulkUpdate",
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    }

    with caplog.at_level("WARNING"):
        api = build(spec, Rules(), english_nlp, unmatched_namespace="ops")

    ops = next(ns for ns in api.namespaces if ns.name == "ops")
    assert {act.attr_override for act in ops.actions} == {
        "bulk_update",
        "bulk_update_2",
    }
    assert {act.name for act in ops.actions} == {"BulkUpdate", "BulkUpdate2"}
    assert "already in use" in caplog.text


def test_build_unmatched_aggregates_ops_from_different_paths(
    english_nlp: Language,
) -> None:
    """Unmatched ops from unrelated paths all land under the same synthetic namespace."""
    spec: dict[str, Any] = {
        "paths": {
            "/users": {
                "put": {
                    "operationId": "bulkUpdateUsers",
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/accounts": {
                "delete": {
                    "operationId": "wipeAccounts",
                    "responses": {"200": {"description": "OK"}},
                },
            },
        }
    }

    api = build(spec, Rules(), english_nlp, unmatched_namespace="ops")

    ops = next(ns for ns in api.namespaces if ns.name == "ops")
    assert {act.attr_override for act in ops.actions} == {
        "bulk_update_users",
        "wipe_accounts",
    }


def test_build_unmatched_preserves_original_path_for_request_emission(
    english_nlp: Language,
) -> None:
    """The synthetic action carries the original HTTP path so request URLs survive."""
    api = build(
        _spec_with_put_on_collection(operation_id="bulkUpdateUsers"),
        Rules(),
        english_nlp,
        unmatched_namespace="ops",
    )

    ops = next(ns for ns in api.namespaces if ns.name == "ops")
    action = next(
        act for act in ops.actions if act.attr_override == "bulk_update_users"
    )
    assert action.path == "/users"


def test_build_unmatched_skips_namespace_when_no_unmatched_ops(
    english_nlp: Language,
) -> None:
    """A clean spec with the flag on adds no synthetic namespace."""
    spec: dict[str, Any] = {
        "paths": {
            "/orders": {"get": {"responses": {"200": {"description": "OK"}}}},
        }
    }

    api = build(spec, Rules(), english_nlp, unmatched_namespace="ops")

    assert [ns.name for ns in api.namespaces] == []
    assert [coll.name for coll in api.collections] == ["Orders"]


def test_build_unmatched_collision_with_top_level_namespace_raises(
    english_nlp: Language,
) -> None:
    """`--unmatched <name>` rejects a name matching a top-level Namespace."""
    spec: dict[str, Any] = {
        "x-okapipy-ns": ["ops"],
        "paths": {
            "/ops/audit": {"get": {"responses": {"200": {"description": "OK"}}}},
            "/users": {"put": {"responses": {"200": {"description": "OK"}}}},
        },
    }

    with pytest.raises(UnmatchedNamespaceCollisionError, match="namespace 'ops'"):
        build(spec, Rules(), english_nlp, unmatched_namespace="ops")


def test_build_unmatched_collision_with_top_level_collection_raises(
    english_nlp: Language,
) -> None:
    """`--unmatched <name>` rejects a name matching a top-level Collection."""
    spec: dict[str, Any] = {
        "paths": {
            "/widgets": {
                "get": {"responses": {"200": {"description": "OK"}}},
                "put": {"responses": {"200": {"description": "OK"}}},
            },
        }
    }

    with pytest.raises(UnmatchedNamespaceCollisionError, match="collection 'Widgets'"):
        build(spec, Rules(), english_nlp, unmatched_namespace="widgets")


def test_build_unmatched_collision_with_top_level_singleton_raises(
    english_nlp: Language,
) -> None:
    """`--unmatched <name>` rejects a name matching a top-level Singleton."""
    spec: dict[str, Any] = {
        "paths": {
            "/me": {
                "x-okapipy-kind": "singleton",
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/users": {"put": {"responses": {"200": {"description": "OK"}}}},
        }
    }

    with pytest.raises(UnmatchedNamespaceCollisionError, match="singleton 'Me'"):
        build(spec, Rules(), english_nlp, unmatched_namespace="me")


def test_build_unmatched_collision_with_top_level_action_raises(
    english_nlp: Language,
) -> None:
    """`--unmatched <name>` rejects a name matching a top-level Action."""
    spec: dict[str, Any] = {
        "paths": {
            "/login": {"post": {"responses": {"200": {"description": "OK"}}}},
            "/users": {"put": {"responses": {"200": {"description": "OK"}}}},
        }
    }

    with pytest.raises(UnmatchedNamespaceCollisionError, match="action 'Login'"):
        build(spec, Rules(), english_nlp, unmatched_namespace="login")


def test_build_unmatched_collision_check_runs_even_with_empty_buffer(
    english_nlp: Language,
) -> None:
    """A colliding `--unmatched` name surfaces even when no ops would be buffered.

    Prevents a stale flag silently passing on a tidy spec: if the customer
    eventually adds an unmatched op the existing top-level collision would
    only surface then. We fail eagerly on every run instead.
    """
    spec: dict[str, Any] = {
        "x-okapipy-ns": ["ops"],
        "paths": {
            "/ops/audit": {"get": {"responses": {"200": {"description": "OK"}}}},
        },
    }

    with pytest.raises(UnmatchedNamespaceCollisionError, match="namespace 'ops'"):
        build(spec, Rules(), english_nlp, unmatched_namespace="ops")


def test_build_unmatched_rejects_blank_name(english_nlp: Language) -> None:
    """An empty / whitespace `unmatched_namespace` is rejected before any tree growth."""
    with pytest.raises(UnmatchedNamespaceCollisionError):
        build(
            _spec_with_put_on_collection(),
            Rules(),
            english_nlp,
            unmatched_namespace="   ",
        )


def test_build_unmatched_buffers_get_on_bare_namespace_path(
    english_nlp: Language,
) -> None:
    """A GET on a path that resolves to a Namespace itself is buffered, not dropped."""
    spec: dict[str, Any] = {
        "x-okapipy-ns": ["admin"],
        "paths": {
            "/admin/health": {
                "get": {
                    "operationId": "adminHealth",
                    "responses": {"200": {"description": "OK"}},
                },
            },
        },
    }

    api = build(spec, Rules(), english_nlp, unmatched_namespace="ops")

    ops = next(ns for ns in api.namespaces if ns.name == "ops")
    health: Action = next(
        act for act in ops.actions if act.attr_override == "admin_health"
    )
    assert health.path == "/admin/health"
    assert [op.method for op in health.operations] == ["GET"]
