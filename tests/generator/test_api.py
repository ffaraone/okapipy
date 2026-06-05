"""Smoke tests for the generator entry point."""

from __future__ import annotations

from pathlib import Path

from okapipy.generator import GenerationError, generate_for_mount
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.model import APIModel

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "simple.yaml"


def _generate_skeleton(tmp_path: Path) -> dict[str, GeneratedFile]:
    """Helper: render the full skeleton against an empty APIModel + a real spec.

    `raw_spec` must be a real OpenAPI document because Phase 4 invokes
    `datamodel-code-generator` to render `models.py`. The empty `APIModel` keeps
    the namespace/collection/resource/action emitters quiet — Phase 6 covers
    those paths.
    """
    return generate_for_mount(
        APIModel(),
        raw_spec=FIXTURE,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )


def test_generate_returns_dict() -> None:
    """`generate(...)` returns a `dict[str, GeneratedFile]` populated with the skeleton.

    The contract is documented in §3.1 of `generator_plan.md`: a virtual FS keyed
    on POSIX-style relative paths; values are `GeneratedFile` records carrying
    content + lifecycle policy.
    """
    vfs = _generate_skeleton(Path("/tmp"))

    assert isinstance(vfs, dict)
    assert all(
        isinstance(k, str) and isinstance(v, GeneratedFile) for k, v in vfs.items()
    )


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
    assert "src/acme/client/base/__init__.py" in vfs
    assert "src/acme/client/base/client.py" in vfs
    assert "src/acme/client/base/models.py" in vfs


def test_skeleton_substitutes_context_variables() -> None:
    """Templated values flow through: client class, package, project name."""
    vfs = _generate_skeleton(Path("/tmp"))

    pyproject = vfs["pyproject.toml"].content
    readme = vfs["README.md"].content
    assert 'name = "client"' in pyproject  # project_name defaults to last segment
    assert "AcmeClient" in readme
    assert "acme.client" in readme


def test_generation_error_is_exported() -> None:
    """`GenerationError` is the documented base class for generator failures."""
    assert issubclass(GenerationError, Exception)


def test_shape_dicts_skips_models_file() -> None:
    """`shape="dicts"` produces a project with no `base/models.py`.

    Drives the `--shape dicts` CLI flow: the skeleton is still emitted, but
    dmcg is not invoked and the models file is absent from the virtual FS.
    The walker's model-import filter then sees an empty set and elides every
    `from ..models import ...` line.
    """
    vfs = generate_for_mount(
        APIModel(),
        raw_spec=FIXTURE,
        output_dir=Path("/tmp"),
        package="acme.client",
        client_class="AcmeClient",
        shape="dicts",
    )

    assert "src/acme/client/base/models.py" not in vfs
    # Skeleton + runtime + client base must still be present.
    assert "pyproject.toml" in vfs
    assert "src/acme/client/base/__init__.py" in vfs
    assert "src/acme/client/base/client.py" in vfs


def test_singletons_fixture_emits_singleton_files(tmp_path: Path) -> None:
    """Parsing `singletons.yaml` and generating produces `base/singletons/*.py`.

    Covers root singletons (`/me`, `/health`) and a sub-singleton under a
    Resource (`/users/{id}/avatar` → singleton named `UserAvatar`). Each
    should land at `src/<package>/base/singletons/<snake_name>.py` with both
    sync and async classes carrying CRUD methods.
    """
    from okapipy.parser.api import parse

    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "singletons.yaml"
    api = parse(fixture, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")

    vfs = generate_for_mount(
        api,
        raw_spec=fixture,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    assert "src/acme/client/base/singletons/me.py" in vfs
    assert "src/acme/client/base/singletons/health.py" in vfs
    assert "src/acme/client/base/singletons/user_avatar.py" in vfs
    me = vfs["src/acme/client/base/singletons/me.py"].content
    assert "class MeSingletonBase" in me
    assert "class AsyncMeSingletonBase" in me
    assert "def retrieve" in me
    assert "def patch" in me
    avatar = vfs["src/acme/client/base/singletons/user_avatar.py"].content
    assert "class UserAvatarSingletonBase" in avatar
    assert "def update" in avatar
    assert "def delete" in avatar


def test_singletons_fixture_wires_client_and_resource(tmp_path: Path) -> None:
    """Generated client exposes `me` and `health` properties; User resource exposes `avatar`.

    Top-level singletons attach as `cached_property` on the client, and a
    sub-singleton attaches as a property on its parent resource via the
    factory-hook pattern used by the rest of the tree.
    """
    from okapipy.parser.api import parse

    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "singletons.yaml"
    api = parse(fixture, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")

    vfs = generate_for_mount(
        api,
        raw_spec=fixture,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    client_src = vfs["src/acme/client/base/client.py"].content
    assert "MeSingletonBase" in client_src
    assert "HealthSingletonBase" in client_src
    assert ".singletons.me" in client_src
    assert ".singletons.health" in client_src
    assert "def me(self) -> MeSingletonBase" in client_src
    assert "def health(self) -> HealthSingletonBase" in client_src
    user_resource = vfs["src/acme/client/base/resources/user.py"].content
    assert "UserAvatarSingletonBase" in user_resource
    assert "..singletons.user_avatar" in user_resource
    assert "def avatar(self) -> UserAvatarSingletonBase" in user_resource


def test_singleton_hosts_sub_collection_in_generated_client(tmp_path: Path) -> None:
    """A spec with a singleton parent and a child collection (`/me/orders`) generates cleanly.

    Singletons may host sub-collections so APIs like `/me/orders`,
    `/orgs/current/members`, and `/workspaces/current/tags` can be modeled
    without restructuring. The generator emits the singleton, the sub-collection,
    and wires the accessor on the singleton class.
    """
    from okapipy.parser.api import parse

    spec_yaml = """
openapi: 3.0.0
info: {title: Sub-collection, version: 1.0.0}
paths:
  /me:
    x-okapipy-kind: singleton
    get:
      summary: Retrieve me
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/User'}
  /me/orders:
    get:
      summary: List my orders
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Order'}
    post:
      summary: Place an order on behalf of me
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/OrderInput'}
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Order'}
components:
  schemas:
    User: {type: object, properties: {id: {type: string}}}
    Order: {type: object, properties: {id: {type: string}}}
    OrderInput: {type: object, properties: {item: {type: string}}}
"""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(spec_yaml, encoding="utf-8")
    api = parse(spec_path, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")

    vfs = generate_for_mount(
        api,
        raw_spec=spec_path,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    assert "src/acme/client/base/singletons/me.py" in vfs
    assert "src/acme/client/base/collections/me_orders.py" in vfs
    me_src = vfs["src/acme/client/base/singletons/me.py"].content
    assert "MeOrdersCollectionBase" in me_src
    assert "def orders(self) -> MeOrdersCollectionBase" in me_src
    orders_src = vfs["src/acme/client/base/collections/me_orders.py"].content
    assert "class MeOrdersCollectionBase" in orders_src
    assert "def all" in orders_src
    assert "def create" in orders_src


def test_collection_hosts_sub_singleton_in_generated_client(tmp_path: Path) -> None:
    """A collection with an aggregate-view sub-singleton (`/orders/stats`) generates cleanly.

    Collections may host singleton "summary" / "stats" endpoints that aggregate
    over the items. The generator emits the singleton file, wires the
    accessor on the collection class, and the user can call
    `client.orders.stats.retrieve()`.
    """
    from okapipy.parser.api import parse

    spec_yaml = """
openapi: 3.0.0
info: {title: Sub-singleton, version: 1.0.0}
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
  /orders/stats:
    x-okapipy-kind: singleton
    get:
      summary: Aggregate stats over orders
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Stats'}
components:
  schemas:
    Order: {type: object, properties: {id: {type: string}}}
    Stats: {type: object, properties: {total: {type: integer}}}
"""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(spec_yaml, encoding="utf-8")
    api = parse(spec_path, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")

    vfs = generate_for_mount(
        api,
        raw_spec=spec_path,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    assert "src/acme/client/base/collections/orders.py" in vfs
    assert "src/acme/client/base/singletons/order_stats.py" in vfs
    orders_src = vfs["src/acme/client/base/collections/orders.py"].content
    assert "OrderStatsSingletonBase" in orders_src
    assert "def stats(self) -> OrderStatsSingletonBase" in orders_src
    stats_src = vfs["src/acme/client/base/singletons/order_stats.py"].content
    assert "class OrderStatsSingletonBase" in stats_src
    assert "def retrieve" in stats_src


def test_root_actions_fixture_emits_action_files(tmp_path: Path) -> None:
    """Root and namespace-level actions land in `base/actions/` and the client/namespace wires them.

    `/login`, `/logout`, `/password-reset` attach to the client root;
    `/auth/refresh` attaches to the auth namespace. All four produce
    `*ActionBase` files and corresponding properties on their parent.
    """
    from okapipy.parser.api import parse

    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "root_actions.yaml"
    api = parse(fixture, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")

    vfs = generate_for_mount(
        api,
        raw_spec=fixture,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    assert "src/acme/client/base/actions/login.py" in vfs
    assert "src/acme/client/base/actions/logout.py" in vfs
    assert "src/acme/client/base/actions/password_reset.py" in vfs
    assert "src/acme/client/base/actions/refresh.py" in vfs
    client_src = vfs["src/acme/client/base/client.py"].content
    assert "def login(self) -> LoginActionBase" in client_src
    assert "def password_reset(self) -> PasswordResetActionBase" in client_src
    auth_ns = vfs["src/acme/client/base/namespaces/auth.py"].content
    assert "RefreshActionBase" in auth_ns
    assert "..actions.refresh" in auth_ns
    assert "def refresh(self) -> RefreshActionBase" in auth_ns


def test_dmcg_class_name_sanitizes_ref_segments() -> None:
    """`_dmcg_class_name` mirrors how dmcg PascalCases generic-style ref names.

    Splits on every non-alphanumeric run, drops empty parts, and capitalizes
    each surviving fragment. Used as a fallback in `_filter_model_name` so a
    parser-recovered ref like `LimitOffsetPage_OrganizationRead_` resolves to
    the dmcg-emitted `LimitOffsetPageOrganizationRead`.
    """
    from okapipy.generator.emit.walk import _dmcg_class_name, _filter_model_name

    assert (
        _dmcg_class_name("LimitOffsetPage_OrganizationRead_")
        == "LimitOffsetPageOrganizationRead"
    )
    assert _dmcg_class_name("Already.Clean") == "AlreadyClean"
    assert _dmcg_class_name("Plain") == "Plain"

    available = {"LimitOffsetPageOrganizationRead", "OrganizationRead"}
    # Verbatim hit returns as-is — no normalization needed.
    assert _filter_model_name("OrganizationRead", available) == "OrganizationRead"
    # Generic-style miss falls through to the sanitized form.
    assert (
        _filter_model_name("LimitOffsetPage_OrganizationRead_", available)
        == "LimitOffsetPageOrganizationRead"
    )
    # Truly unknown name still drops to None.
    assert _filter_model_name("Nonexistent", available) is None


def test_anyof_request_body_renders_as_python_union_in_action(tmp_path: Path) -> None:
    """An action whose body is `anyOf: [$ref Login, $ref RefreshAccessToken]` types
    `body` as `Login | RefreshAccessToken`, and the action file imports both classes.

    Without member-aware handling, the parser would fall back to the schema's
    `title` (or no name), the walker would filter it out, and the body parameter
    would degrade to `Any`.
    """
    import yaml

    from okapipy.parser.api import parse

    spec = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/login": {
                "post": {
                    "x-okapipy-kind": "action",
                    "summary": "Get token",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [
                                        {"$ref": "#/components/schemas/Login"},
                                        {
                                            "$ref": "#/components/schemas/RefreshAccessToken"
                                        },
                                    ],
                                    "title": "Data",
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Login": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "password": {"type": "string"},
                    },
                    "required": ["email", "password"],
                },
                "RefreshAccessToken": {
                    "type": "object",
                    "properties": {
                        "refresh_token": {"type": "string"},
                    },
                    "required": ["refresh_token"],
                },
            },
        },
    }
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec))

    api = parse(spec_path, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")
    vfs = generate_for_mount(
        api,
        raw_spec=spec_path,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    action_src = vfs["src/acme/client/base/actions/login.py"].content
    assert "body: Login | RefreshAccessToken | dict[str, Any]" in action_src
    # Both sync and async `run` methods carry the same union signature.
    assert action_src.count("body: Login | RefreshAccessToken | dict[str, Any]") == 2
    assert "from ..models import Login, RefreshAccessToken" in action_src


def test_inline_body_matching_existing_component_is_not_duplicated(
    tmp_path: Path,
) -> None:
    """When a path's inline request body is shape-identical to a top-level component,
    the flattener reuses the existing component name — dmcg emits a single class.

    The check inspects `models.py` directly: the spec author named one schema
    `Login`, so there must be exactly one `class Login(...)` definition and no
    `Login<hash>` siblings.
    """
    import yaml

    from okapipy.parser.api import parse

    login_shape = {
        "type": "object",
        "properties": {
            "email": {"type": "string"},
            "password": {"type": "string"},
        },
        "required": ["email", "password"],
        "title": "Login",
    }
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/login": {
                "post": {
                    "x-okapipy-kind": "action",
                    "requestBody": {
                        "content": {"application/json": {"schema": dict(login_shape)}},
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {"Login": login_shape}},
    }
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(yaml.safe_dump(spec))

    api = parse(spec_path, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")
    vfs = generate_for_mount(
        api,
        raw_spec=spec_path,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    models_src = vfs["src/acme/client/base/models.py"].content
    assert models_src.count("\nclass Login(") == 1
    # No hash-suffixed Login lookalike.
    assert "class Login0" not in models_src
    assert "class Login1" not in models_src


def test_shape_dicts_drops_model_imports_from_generated_nodes(
    tmp_path: Path,
) -> None:
    """With `shape="dicts"`, generated collection/resource/action files have no
    `from ..models import ...` line — all model references are filtered out.

    The fixture exposes `/orders` (collection) and `/orders/{id}` (resource)
    that would normally pull `Order` / `OrderList` model imports; with
    `shape="dicts"` those imports must vanish so the generated package stays
    compilable in the absence of `models.py`.
    """
    from okapipy.parser.api import parse  # local import to avoid cycle at module load

    api = parse(FIXTURE, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")

    vfs = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
        shape="dicts",
    )

    for path, file in vfs.items():
        if not path.startswith("src/acme/client/base/"):
            continue
        assert "from ..models import" not in file.content, path
        assert "from .base.models import" not in file.content, path


def test_collection_iterator_item_type_excludes_none(tmp_path: Path) -> None:
    """The iterator's per-item type omits `None`; only `first()` admits it.

    `__next__` / `__anext__` raise `StopIteration` / `StopAsyncIteration` to
    signal exhaustion — they never yield `None`. So the `Iterator[...]`
    parameter, the `current_page: list[...]` storage, and the `__next__`
    return annotation must all be free of `| None`. The `first()` accessor
    is the only spot that legitimately returns `None` (empty collection),
    and its annotation keeps the `| None` arm.
    """
    from okapipy.parser.api import parse

    api = parse(FIXTURE, nlp_cache_dir=Path(__file__).resolve().parents[2] / ".spacy")
    vfs = generate_for_mount(
        api,
        raw_spec=FIXTURE,
        output_dir=tmp_path / "out",
        package="acme.client",
        client_class="AcmeClient",
    )

    orders_src = vfs["src/acme/client/base/collections/orders.py"].content

    assert (
        "class OrdersCollectionBaseIterator(Iterator[Order | dict[str, Any]]):"
        in orders_src
    )
    assert (
        "class AsyncOrdersCollectionBaseIterator(AsyncIterator[Order | dict[str, Any]]):"
        in orders_src
    )
    assert "self.current_page: list[Order | dict[str, Any]] = []" in orders_src
    assert "def __next__(self) -> Order | dict[str, Any]:" in orders_src
    assert "async def __anext__(self) -> Order | dict[str, Any]:" in orders_src
    assert "Iterator[Order | dict[str, Any] | None]" not in orders_src
    assert "AsyncIterator[Order | dict[str, Any] | None]" not in orders_src
    assert "def __next__(self) -> Order | dict[str, Any] | None:" not in orders_src
    assert (
        "async def __anext__(self) -> Order | dict[str, Any] | None:" not in orders_src
    )

    assert "def first(self) -> Order | dict[str, Any] | None:" in orders_src
    assert "async def first(self) -> Order | dict[str, Any] | None:" in orders_src
