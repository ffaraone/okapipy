"""Tests for the builder, naming engine, and operation routing."""

from __future__ import annotations

from pathlib import Path

import pytest
from spacy.language import Language

from okapipy.parser.builder import (
    build,
    contextual_name,
    singularize,
)
from okapipy.parser.loader import load_spec
from okapipy.parser.model import APIModel, Collection, Namespace
from okapipy.parser.rules import Rules, load_rules


def test_contextual_name_with_empty_breadcrumb_returns_pascal_case() -> None:
    """A bare segment with no parent yields just its PascalCase form."""
    assert contextual_name([], "orders") == "Orders"


def test_contextual_name_concatenates_parent_and_segment() -> None:
    """A non-empty breadcrumb produces `<Parent><Segment>`."""
    assert contextual_name(["Order"], "lines") == "OrderLines"


def test_contextual_name_normalizes_kebab_case() -> None:
    """Hyphens are stripped during PascalCase rendering of the segment."""
    assert contextual_name(["User"], "reset-password") == "UserResetPassword"


def test_singularize_returns_lemma_for_plural_noun(english_nlp: Language) -> None:
    """A plural noun like `orders` is reduced to its singular lemma `order`."""
    assert singularize("orders", english_nlp) == "order"


def test_singularize_leaves_singular_unchanged(english_nlp: Language) -> None:
    """A singular noun is returned as-is, with no spurious lemmatization."""
    assert singularize("commerce", english_nlp) == "commerce"


def test_singularize_only_lemmatizes_the_head_of_a_compound(
    english_nlp: Language,
) -> None:
    """In `password-recovery-requests` only the trailing `requests` is reduced."""
    assert (
        singularize("password-recovery-requests", english_nlp)
        == "password-recovery-request"
    )


def test_singularize_handles_propn_lemma_via_wrapper(english_nlp: Language) -> None:
    """`tokens` (PROPN in isolation) still yields `token` thanks to the wrapper lemma."""
    assert singularize("tokens", english_nlp) == "token"


def test_build_treats_force_reimport_as_resource_action(english_nlp: Language) -> None:
    """A `/orgs/{id}/datasources/{ds}/force-reimport` POST attaches as a resource action.

    The action name reflects the full breadcrumb chain so it is unique even when the
    same trailing word appears under multiple parents.
    """
    spec = {
        "paths": {
            "/organizations/{organization_id}/datasources/{datasource_id}/force-reimport": {
                "parameters": [
                    {
                        "name": "organization_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "datasource_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "post": {"responses": {"200": {"description": "OK"}}},
            },
        }
    }

    api = build(spec, Rules(), english_nlp)

    organizations = api.collections[0]
    assert organizations.name == "Organizations"
    assert organizations.resource is not None
    datasources = organizations.resource.collections[0]
    assert datasources.name == "OrganizationDatasources"
    assert datasources.resource is not None
    actions = datasources.resource.actions
    assert len(actions) == 1
    assert actions[0].name == "OrganizationDatasourceForceReimport"


def test_build_drops_post_on_resource_path_when_not_marked_as_action(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """POST on `/auth/password-recovery-requests/{email}` is dropped, not coerced.

    A POST whose path ends in a path parameter doesn't fit the
    namespace/collection/resource/action hierarchy and the user has not marked it
    with `x-okapipy-kind: action`, so the parser logs a warning and skips it.
    """
    spec = {
        "x-okapipy-ns": ["auth"],
        "paths": {
            "/auth/password-recovery-requests/{email}": {
                "parameters": [
                    {
                        "name": "email",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }

    with caplog.at_level("WARNING"):
        api = build(spec, Rules(), english_nlp)

    auth = next(ns for ns in api.namespaces if ns.name == "auth")
    collection = auth.collections[0]
    assert collection.name == "PasswordRecoveryRequests"
    assert collection.resource is not None
    assert collection.resource.actions == []
    assert "POST /auth/password-recovery-requests/{email}" in caplog.text


def test_build_resource_name_for_compound_collection(english_nlp: Language) -> None:
    """A `/password-recovery-requests/{id}` resource is named PasswordRecoveryRequest."""
    spec = {
        "paths": {
            "/password-recovery-requests": {
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/password-recovery-requests/{id}": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {"responses": {"200": {"description": "OK"}}},
            },
        }
    }

    api = build(spec, Rules(), english_nlp)

    collection = api.collections[0]
    assert collection.name == "PasswordRecoveryRequests"
    assert collection.resource is not None
    assert collection.resource.name == "PasswordRecoveryRequest"


def test_build_handles_empty_paths_object(english_nlp: Language) -> None:
    """A spec with no paths yields an empty APIModel without error."""
    api = build({"paths": {}}, Rules(), english_nlp)

    assert api == APIModel()


def test_build_creates_top_level_collection_for_simple_spec(
    simple_spec_path: Path, english_nlp: Language
) -> None:
    """A spec with `/orders` and `/orders/{id}` yields one root collection with a resource."""
    spec = load_spec(simple_spec_path)

    api = build(spec, Rules(), english_nlp)

    assert len(api.collections) == 1
    orders = api.collections[0]
    assert orders.name == "Orders"
    assert orders.path == "/orders"
    assert orders.fetch is not None
    assert orders.fetch.method == "GET"
    assert orders.resource is not None
    assert orders.resource.name == "Order"
    assert orders.resource.retrieve is not None


def test_build_uses_contextual_name_for_subcollection(
    nested_spec_path: Path, english_nlp: Language
) -> None:
    """A `/orders/{id}/lines` subcollection is named `OrderLines` via the breadcrumb."""
    spec = load_spec(nested_spec_path)

    api = build(spec, Rules(), english_nlp)

    commerce = _find_namespace(api, "commerce")
    orders = next(c for c in commerce.collections if c.name == "Orders")
    assert orders.resource is not None
    sub = orders.resource.collections
    assert [c.name for c in sub] == ["OrderLines"]
    order_lines = sub[0]
    assert order_lines.resource is not None
    assert order_lines.resource.name == "OrderLine"


def test_build_attaches_action_under_resource_when_extension_set(
    nested_spec_path: Path, english_nlp: Language
) -> None:
    """`/orders/{id}/submit` with `x-okapipy-kind: action` becomes a Resource-level action."""
    spec = load_spec(nested_spec_path)

    api = build(spec, Rules(), english_nlp)

    commerce = _find_namespace(api, "commerce")
    orders = next(c for c in commerce.collections if c.name == "Orders")
    assert orders.resource is not None
    actions = orders.resource.actions
    assert len(actions) == 1
    submit = actions[0]
    assert submit.name == "OrderSubmit"
    assert submit.path == "/commerce/orders/{id}/submit"
    assert [op.method for op in submit.operations] == ["POST"]


def test_build_routes_methods_to_canonical_resource_slots(
    nested_spec_path: Path, english_nlp: Language
) -> None:
    """GET and DELETE on `/orders/{id}` land on `retrieve` and `delete` respectively."""
    spec = load_spec(nested_spec_path)

    api = build(spec, Rules(), english_nlp)

    commerce = _find_namespace(api, "commerce")
    orders = next(c for c in commerce.collections if c.name == "Orders")
    assert orders.resource is not None
    assert orders.resource.retrieve is not None
    assert orders.resource.delete is not None
    assert orders.resource.update is None


def test_build_recovers_request_and_response_model_names(
    nested_spec_path: Path, english_nlp: Language
) -> None:
    """The original `$ref` schema names are recovered for request and response models."""
    spec = load_spec(nested_spec_path)

    api = build(spec, Rules(), english_nlp)

    commerce = _find_namespace(api, "commerce")
    orders = next(c for c in commerce.collections if c.name == "Orders")
    create = orders.create
    assert create is not None
    assert create.request_model == "Order"
    assert create.response_model == "Order"


def test_build_uses_rules_namespace_registry(
    nested_spec_path: Path, english_nlp: Language, tmp_path: Path
) -> None:
    """A namespace declared only in the rules file is recognized at build time."""
    rules_yaml = tmp_path / "side.yaml"
    rules_yaml.write_text("x-okapipy-ns:\n  - commerce\n")
    spec = load_spec(nested_spec_path)
    # remove the in-spec registry so only the rules file contributes
    spec.pop("x-okapipy-ns", None)

    api = build(spec, load_rules(rules_yaml), english_nlp)

    assert _find_namespace(api, "commerce").name == "commerce"


def test_build_strips_server_base_path(english_nlp: Language) -> None:
    """The path component of the first `servers[].url` is stripped before classification."""
    spec = {
        "servers": [{"url": "https://example.com/api/v1"}],
        "paths": {
            "/api/v1/orders": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }

    api = build(spec, Rules(), english_nlp)

    assert [c.path for c in api.collections] == ["/orders"]


def test_build_attaches_namespace_level_action(
    english_nlp: Language,
) -> None:
    """A verb-phrase segment under a namespace becomes a namespace-level Action.

    `/commerce/ping` POST attaches as an Action directly on the `commerce`
    Namespace. Namespaces don't enter the breadcrumb, so the Action's name is
    just `Ping`.
    """
    spec = {
        "x-okapipy-ns": ["commerce"],
        "paths": {
            "/commerce/ping": {
                "post": {
                    "x-okapipy-kind": "action",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    api = build(spec, Rules(), english_nlp)

    commerce = next(ns for ns in api.namespaces if ns.name == "commerce")
    assert commerce.collections == []
    assert len(commerce.actions) == 1
    ping = commerce.actions[0]
    assert ping.name == "Ping"
    assert ping.path == "/commerce/ping"
    assert [op.method for op in ping.operations] == ["POST"]


def test_build_drops_op_on_bare_namespace_path_with_warning(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """A GET on a path that resolves to a Namespace itself is dropped, not coerced.

    A namespace is a folder, not an endpoint. When the terminal segment is the
    namespace and no `x-okapipy-kind` re-classifies it, the parser logs a
    warning and skips the operation.
    """
    spec = {
        "x-okapipy-ns": ["commerce"],
        "paths": {
            "/commerce": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }

    with caplog.at_level("WARNING"):
        api = build(spec, Rules(), english_nlp)

    commerce = next(ns for ns in api.namespaces if ns.name == "commerce")
    assert commerce.collections == []
    assert commerce.actions == []
    assert "GET /commerce" in caplog.text
    assert "bare namespace path" in caplog.text


def test_build_skips_non_canonical_collection_method_with_warning(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """A bare PUT on a collection has no canonical slot and no `x-okapipy-kind: action`.

    Such operations don't fit the namespace/collection/resource/action hierarchy, so
    the parser drops them with a warning rather than fabricating a synthetic action.
    """
    spec = {
        "paths": {
            "/orders": {
                "put": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    with caplog.at_level("WARNING"):
        api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is None
    assert orders.create is None
    assert orders.actions == []
    assert "PUT /orders" in caplog.text


def test_build_skips_non_canonical_resource_method_with_warning(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """A bare POST on a resource path is dropped and a warning is logged.

    The hierarchy has no slot for `POST /items/{id}` (Resource has no `create`), and
    treating it as an action via the path parameter would yield a meaningless name.
    """
    spec = {
        "paths": {
            "/orders/{id}": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    with caplog.at_level("WARNING"):
        api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.resource is not None
    assert orders.resource.actions == []
    assert "POST /orders/{id}" in caplog.text


def test_build_groups_methods_on_action_path(english_nlp: Language) -> None:
    """Multiple methods on an action path land as multiple operations on the same Action."""
    spec = {
        "paths": {
            "/orders/{id}/submit": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "post": {
                    "x-okapipy-kind": "action",
                    "responses": {"200": {"description": "OK"}},
                },
                "put": {
                    "x-okapipy-kind": "action",
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.resource is not None
    submit = orders.resource.actions[0]
    assert sorted(op.method for op in submit.operations) == ["POST", "PUT"]


def test_build_handles_request_body_without_ref(english_nlp: Language) -> None:
    """A request body declared inline (no `$ref`) falls back to the schema's `title`."""
    spec = {
        "paths": {
            "/orders": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "title": "OrderInput"}
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.create is not None
    assert orders.create.request_model == "OrderInput"


def test_build_routes_resource_put_to_update_slot(english_nlp: Language) -> None:
    """PUT on a resource lands on the canonical `update` slot."""
    spec = {
        "paths": {
            "/orders/{id}": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "put": {"responses": {"200": {"description": "OK"}}},
                "patch": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.resource is not None
    assert orders.resource.update is not None
    assert orders.resource.partial_update is not None


def test_build_routes_collection_method_with_action_hint_to_synthetic_action(
    english_nlp: Language,
) -> None:
    """A per-method `x-okapipy-kind: action` on a collection sends the op to a synthetic action."""
    spec = {
        "paths": {
            "/orders": {
                "get": {
                    "x-okapipy-kind": "action",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.fetch is None
    assert len(orders.actions) == 1


def test_build_routes_resource_method_with_action_hint_to_synthetic_action(
    english_nlp: Language,
) -> None:
    """A per-method `x-okapipy-kind: action` on a resource path routes to a resource action."""
    spec = {
        "paths": {
            "/orders/{id}": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "get": {
                    "x-okapipy-kind": "action",
                    "responses": {"200": {"description": "OK"}},
                },
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    orders = api.collections[0]
    assert orders.resource is not None
    assert orders.resource.retrieve is None
    assert len(orders.resource.actions) == 1


def test_build_strips_explicit_prefix(english_nlp: Language) -> None:
    """An explicit `strip_prefix` removes the prefix and overrides `servers[].url`."""
    spec = {
        "servers": [{"url": "https://example.com/wrong-base"}],
        "paths": {
            "/public/v1/orders": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }

    api = build(spec, Rules(), english_nlp, strip_prefix="/public/v1")

    assert len(api.collections) == 1
    assert api.collections[0].name == "Orders"
    assert api.collections[0].path == "/orders"


def test_build_strips_explicit_prefix_when_servers_absent(
    english_nlp: Language,
) -> None:
    """`strip_prefix` works on its own when the spec has no `servers` declared."""
    spec = {
        "paths": {
            "/api/v2/users": {
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp, strip_prefix="/api/v2")

    assert api.collections[0].name == "Users"


def test_build_skips_paths_with_only_a_root_segment(english_nlp: Language) -> None:
    """A spec that only declares the root path `/` is parsed without errors and yields nothing."""
    api = build(
        {"paths": {"/": {"get": {"responses": {"200": {"description": "OK"}}}}}},
        Rules(),
        english_nlp,
    )

    assert api.namespaces == []
    assert api.collections == []


def test_build_attaches_root_level_action(english_nlp: Language) -> None:
    """A verb path at the root (`/login`) becomes a root-level Action on APIModel.

    The breadcrumb is empty so the Action's name is just the PascalCase of the
    segment, and the POST method is appended to its operations.
    """
    spec = {
        "paths": {
            "/login": {
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    assert len(api.actions) == 1
    login = api.actions[0]
    assert login.name == "Login"
    assert login.path == "/login"
    assert [op.method for op in login.operations] == ["POST"]


def test_build_attaches_root_action_for_kebab_verb_phrase(
    english_nlp: Language,
) -> None:
    """`/password-reset` at the root attaches as Action `PasswordReset`."""
    spec = {
        "paths": {
            "/password-reset": {
                "post": {"responses": {"202": {"description": "Accepted"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    assert [a.name for a in api.actions] == ["PasswordReset"]


def test_build_attaches_singleton_at_root(english_nlp: Language) -> None:
    """`/me` with `x-okapipy-kind: singleton` becomes a root Singleton.

    GET maps to `retrieve` and PATCH maps to `partial_update`, the same CRUD
    routing used for Resource. The breadcrumb is empty so the name is `Me`.
    """
    spec = {
        "paths": {
            "/me": {
                "x-okapipy-kind": "singleton",
                "get": {"responses": {"200": {"description": "OK"}}},
                "patch": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    assert len(api.singletons) == 1
    me = api.singletons[0]
    assert me.name == "Me"
    assert me.path == "/me"
    assert me.retrieve is not None
    assert me.partial_update is not None
    assert me.update is None
    assert me.delete is None


def test_build_attaches_singleton_under_namespace(english_nlp: Language) -> None:
    """A namespace-scoped singleton (`/admin/health`) attaches under the namespace.

    Namespaces don't enter the breadcrumb, so the Singleton's name remains
    `Health` — disambiguation by namespace is left to downstream generators.
    """
    spec = {
        "x-okapipy-ns": ["admin"],
        "paths": {
            "/admin/health": {
                "x-okapipy-kind": "singleton",
                "get": {"responses": {"200": {"description": "OK"}}},
            }
        },
    }

    api = build(spec, Rules(), english_nlp)

    admin = _find_namespace(api, "admin")
    assert len(admin.singletons) == 1
    health = admin.singletons[0]
    assert health.name == "Health"
    assert health.path == "/admin/health"
    assert health.retrieve is not None


def test_build_attaches_sub_singleton_under_resource(english_nlp: Language) -> None:
    """`/users/{id}/avatar` with singleton hint attaches under the User resource.

    The breadcrumb at the avatar segment is `[User]`, so the name is
    `UserAvatar`. PUT/DELETE route to `update`/`delete` on the Singleton.
    """
    spec = {
        "paths": {
            "/users/{id}/avatar": {
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "x-okapipy-kind": "singleton",
                "get": {"responses": {"200": {"description": "OK"}}},
                "put": {"responses": {"200": {"description": "OK"}}},
                "delete": {"responses": {"204": {"description": "No Content"}}},
            }
        }
    }

    api = build(spec, Rules(), english_nlp)

    users = api.collections[0]
    assert users.resource is not None
    assert len(users.resource.singletons) == 1
    avatar = users.resource.singletons[0]
    assert avatar.name == "UserAvatar"
    assert avatar.path == "/users/{id}/avatar"
    assert avatar.retrieve is not None
    assert avatar.update is not None
    assert avatar.delete is not None


def test_build_skips_non_canonical_singleton_method_with_warning(
    english_nlp: Language, caplog: pytest.LogCaptureFixture
) -> None:
    """A bare POST on a singleton path (no `x-okapipy-kind: action`) is dropped.

    Singletons accept the same CRUD verbs as resources; POST has no slot, and
    coercing it into an action without an explicit opt-in would be surprising.
    """
    spec = {
        "paths": {
            "/me": {
                "x-okapipy-kind": "singleton",
                "post": {"responses": {"200": {"description": "OK"}}},
            }
        }
    }

    with caplog.at_level("WARNING"):
        api = build(spec, Rules(), english_nlp)

    assert api.singletons[0].name == "Me"
    assert api.singletons[0].actions == []
    assert "POST /me" in caplog.text
    assert "singleton" in caplog.text


def test_build_action_under_singleton(english_nlp: Language) -> None:
    """An action segment under a singleton (`/me/refresh`) attaches as a Singleton action.

    Combined with `x-okapipy-kind: singleton` on `/me`, a POST to
    `/me/refresh` lands as an Action on the Me singleton.
    """
    spec = {
        "paths": {
            "/me": {
                "x-okapipy-kind": "singleton",
                "get": {"responses": {"200": {"description": "OK"}}},
            },
            "/me/refresh": {
                "post": {
                    "x-okapipy-kind": "action",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        }
    }

    api = build(spec, Rules(), english_nlp)

    me = api.singletons[0]
    assert me.name == "Me"
    assert len(me.actions) == 1
    refresh = me.actions[0]
    assert refresh.name == "Refresh"
    assert refresh.path == "/me/refresh"
    assert [op.method for op in refresh.operations] == ["POST"]


def test_build_root_actions_fixture(
    root_actions_spec_path: Path, english_nlp: Language
) -> None:
    """End-to-end: parsing `root_actions.yaml` yields the expected tree shape."""
    spec = load_spec(root_actions_spec_path)

    api = build(spec, Rules(), english_nlp)

    assert sorted(a.name for a in api.actions) == ["Login", "Logout", "PasswordReset"]
    auth = _find_namespace(api, "auth")
    assert [a.name for a in auth.actions] == ["Refresh"]


def test_build_singletons_fixture(
    singletons_spec_path: Path, english_nlp: Language
) -> None:
    """End-to-end: `singletons.yaml` yields a root singleton, sub-singleton, and collection."""
    spec = load_spec(singletons_spec_path)

    api = build(spec, Rules(), english_nlp)

    assert {s.name for s in api.singletons} == {"Me", "Health"}
    me = next(s for s in api.singletons if s.name == "Me")
    assert me.retrieve is not None
    assert me.partial_update is not None
    users = next(c for c in api.collections if c.name == "Users")
    assert users.resource is not None
    assert [s.name for s in users.resource.singletons] == ["UserAvatar"]
    avatar = users.resource.singletons[0]
    assert avatar.update is not None
    assert avatar.delete is not None


def _find_namespace(api: APIModel | Namespace, name: str) -> Namespace:
    """Return the namespace child with the given name, raising if absent."""
    for ns in api.namespaces:
        if ns.name == name:
            return ns
    raise AssertionError(f"namespace {name!r} not found")


def _expect_collection(parent: Namespace, name: str) -> Collection:
    """Helper that mirrors `_find_namespace` for collections — used in extra assertions."""
    for col in parent.collections:
        if col.name == name:
            return col
    raise AssertionError(f"collection {name!r} not found under {parent.name!r}")
