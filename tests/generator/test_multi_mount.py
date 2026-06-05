"""End-to-end tests for multi-spec generation via `okapipy.generator.generate(manifest)`.

A real multi-spec project mounts two or more OpenAPI documents under
distinct namespaces in one generated package. The tests here exercise:

* per-mount sub-tree layout (`base/<mount>/...`),
* synthetic mount-namespace classes with the `Mount` suffix,
* per-mount `models.py` so cross-spec class-name collisions can't
  happen at the import level,
* the client class composing every mount accessor.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from okapipy.generator import generate
from okapipy.manifest import load_manifest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def two_mount_manifest(tmp_path: Path) -> Callable[..., Path]:
    """Return a factory that writes a two-mount manifest under `tmp_path`.

    Mounts the `simple` fixture under `users` and the `singletons` fixture
    under `admin`, both with `shape: dicts` so the test stays fast (no
    datamodel-code-generator round-trip).
    """

    def _factory(
        *,
        users_namespace: str = "users",
        admin_namespace: str = "admin",
        shape: str = "dicts",
    ) -> Path:
        payload = {
            "package": "acme.multi",
            "client_class": "MultiClient",
            "shape": shape,
            "output": str(tmp_path / "out"),
            "specs": [
                {
                    "namespace": users_namespace,
                    "source": str(FIXTURES / "simple.yaml"),
                },
                {
                    "namespace": admin_namespace,
                    "source": str(FIXTURES / "singletons.yaml"),
                },
            ],
        }
        path = tmp_path / "okapipy.yml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    return _factory


def test_two_mounts_emit_isolated_base_subtrees(
    two_mount_manifest: Callable[..., Path],
) -> None:
    """Each non-root mount gets its own `base/<mount>/...` sub-tree."""
    manifest = load_manifest(two_mount_manifest())

    vfs = generate(manifest)

    base_paths = {p for p in vfs if "/base/" in p}
    # Each mount owns its own collections / singletons sub-trees.
    assert any("/base/users/collections/" in p for p in base_paths)
    assert any("/base/admin/singletons/" in p for p in base_paths)
    # The vendored runtime stays project-wide (one copy at base/ root).
    assert "src/acme/multi/base/exceptions.py" in base_paths
    assert "src/acme/multi/base/users/exceptions.py" not in base_paths
    assert "src/acme/multi/base/admin/exceptions.py" not in base_paths


def test_each_mount_emits_its_own_init_with_mount_class(
    two_mount_manifest: Callable[..., Path],
) -> None:
    """`base/<mount>/__init__.py` carries the synthetic `<Mount>MountBase` class."""
    manifest = load_manifest(two_mount_manifest())

    vfs = generate(manifest)

    users_init = vfs["src/acme/multi/base/users/__init__.py"].content
    admin_init = vfs["src/acme/multi/base/admin/__init__.py"].content
    assert "class UsersMountBase:" in users_init
    assert "class AsyncUsersMountBase:" in users_init
    assert "class AdminMountBase:" in admin_init
    assert "class AsyncAdminMountBase:" in admin_init


def test_client_imports_and_wires_each_mount(
    two_mount_manifest: Callable[..., Path],
) -> None:
    """`base/client.py` imports and exposes one accessor per mount."""
    manifest = load_manifest(two_mount_manifest())

    vfs = generate(manifest)

    client = vfs["src/acme/multi/base/client.py"].content
    assert "from .users import AsyncUsersMountBase, UsersMountBase" in client
    assert "from .admin import AdminMountBase, AsyncAdminMountBase" in client
    assert "def users(self) -> UsersMountBase:" in client
    assert "def admin(self) -> AdminMountBase:" in client
    assert "def users(self) -> AsyncUsersMountBase:" in client
    assert "def admin(self) -> AsyncAdminMountBase:" in client


def test_user_layer_mount_init_subclasses_mount_base(
    two_mount_manifest: Callable[..., Path],
) -> None:
    """`src/<pkg>/<mount>/__init__.py` is a one-shot stub subclassing `<Mount>MountBase`."""
    manifest = load_manifest(two_mount_manifest())

    vfs = generate(manifest)

    users_user = vfs["src/acme/multi/users/__init__.py"]
    assert users_user.one_shot is True
    assert "class UsersMount(UsersMountBase):" in users_user.content
    assert "class AsyncUsersMount(AsyncUsersMountBase):" in users_user.content


def test_user_layer_client_wires_every_mount(
    two_mount_manifest: Callable[..., Path],
) -> None:
    """`src/<pkg>/client.py` wires `__users_factory__` and `__admin_factory__`."""
    manifest = load_manifest(two_mount_manifest())

    vfs = generate(manifest)

    client = vfs["src/acme/multi/client.py"].content
    assert "__users_factory__ = UsersMount" in client
    assert "__admin_factory__ = AdminMount" in client
    assert "__users_factory__ = AsyncUsersMount" in client
    assert "__admin_factory__ = AsyncAdminMount" in client


def test_mount_with_same_name_as_internal_namespace_does_not_collide(
    tmp_path: Path,
) -> None:
    """A mount named `auth` whose spec has a top-level `auth` namespace renders both distinctly.

    The mount class lands at `base/auth/__init__.py:AuthMountBase`; the
    spec's own namespace at `base/auth/namespaces/auth.py:AuthNamespaceBase`.
    The `Mount` suffix disambiguates the two — without it the imported
    `AuthNamespaceBase` would shadow the synthetic one in the same
    module.
    """
    spec_yaml = """
openapi: 3.0.0
info: {title: Auth, version: 1.0.0}
paths:
  /auth/tokens:
    get:
      summary: List tokens
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema: {$ref: '#/components/schemas/Token'}
components:
  schemas:
    Token: {type: object, properties: {value: {type: string}}}
"""
    spec_path = tmp_path / "auth.yaml"
    spec_path.write_text(spec_yaml, encoding="utf-8")
    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package": "acme.auth_collide",
                "client_class": "AuthClient",
                "shape": "dicts",
                "output": str(tmp_path / "out"),
                "specs": [{"namespace": "auth", "source": str(spec_path)}],
            }
        ),
        encoding="utf-8",
    )

    vfs = generate(load_manifest(manifest_path))

    mount_init = vfs["src/acme/auth_collide/base/auth/__init__.py"].content
    assert "class AuthMountBase:" in mount_init
    # The mount imports the spec's own `auth` namespace under a distinct name.
    assert "from .namespaces.auth import" in mount_init
    assert "AuthNamespaceBase" in mount_init


def test_per_mount_models_files_emitted_under_each_mount(tmp_path: Path) -> None:
    """In `shape: models`, each mount produces its own `models.py` to isolate dmcg output."""
    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package": "acme.models",
                "client_class": "ModelsClient",
                "shape": "models",
                "output": str(tmp_path / "out"),
                "specs": [
                    {"namespace": "users", "source": str(FIXTURES / "simple.yaml")},
                    {
                        "namespace": "admin",
                        "source": str(FIXTURES / "singletons.yaml"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    vfs = generate(load_manifest(manifest_path))

    assert "src/acme/models/base/users/models.py" in vfs
    assert "src/acme/models/base/admin/models.py" in vfs
    # No top-level models.py — every mount owns its own.
    assert "src/acme/models/base/models.py" not in vfs


def test_dotted_mount_namespace_raises_until_supported(tmp_path: Path) -> None:
    """A dotted mount (`platform.users`) raises `GenerationError` at planning time."""
    from okapipy.generator.errors import GenerationError

    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package": "acme.dotted",
                "client_class": "DottedClient",
                "shape": "dicts",
                "output": str(tmp_path / "out"),
                "specs": [
                    {
                        "namespace": "platform.users",
                        "source": str(FIXTURES / "simple.yaml"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GenerationError, match="dotted mount"):
        generate(load_manifest(manifest_path))


def test_root_mount_manifest_still_works(tmp_path: Path) -> None:
    """A single-spec root-mount manifest produces a flat layout (no `<mount>/` subdir)."""
    manifest_path = tmp_path / "okapipy.yml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "package": "acme.flat",
                "client_class": "FlatClient",
                "shape": "dicts",
                "output": str(tmp_path / "out"),
                "specs": [{"namespace": "", "source": str(FIXTURES / "simple.yaml")}],
            }
        ),
        encoding="utf-8",
    )

    vfs = generate(load_manifest(manifest_path))

    # Today's flat layout — no mount sub-tree.
    assert "src/acme/flat/base/collections/orders.py" in vfs
    assert not any("/base/users/" in p for p in vfs)
