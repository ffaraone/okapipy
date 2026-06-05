"""Tests for the cross-run generated-state file written under `base/_generated.json`.

The state file records every regenerated base file (drives pruning) and every
parent → child wiring in the parser tree (drives drift detection).
"""

from __future__ import annotations

import json
from pathlib import Path

from okapipy.generator.edges import compute_edges
from okapipy.generator.state import (
    GENERATOR_VERSION,
    STATE_FILENAME,
    Edge,
    parse,
)
from okapipy.generator.vfs import GeneratedFile
from okapipy.parser.api import parse as parse_spec

NESTED = Path(__file__).resolve().parent.parent / "fixtures" / "nested.yaml"


def test_state_path_in_vfs(state_vfs: dict[str, GeneratedFile]) -> None:
    """A state file is emitted at `src/{pkg}/base/_generated.json`."""
    expected = f"src/man/client/base/{STATE_FILENAME}"

    assert expected in state_vfs
    assert state_vfs[expected].one_shot is False  # regenerated each run


def test_state_is_well_formed_json(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """The state file is parseable JSON with the expected top-level keys."""
    state_text = state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content
    payload = json.loads(state_text)

    assert payload["generator_version"] == GENERATOR_VERSION
    assert isinstance(payload["generated_at"], str)
    assert payload["generated_at"]
    assert isinstance(payload["base_files"], list)
    assert payload["base_files"]
    assert isinstance(payload["edges"], list)
    assert payload["edges"]


def test_base_files_lists_every_emitted_base_file(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """Every VFS path under `<pkg>/base/` is recorded in `base_files`."""
    state = parse(state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content)
    base_paths_in_vfs = {p for p in state_vfs if "/base/" in p}

    assert set(state.base_files) == base_paths_in_vfs


def test_edges_match_compute_edges_directly(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """The state file's `edges` is exactly what `compute_edges(api, package)` produces."""
    api = parse_spec(NESTED)
    direct = compute_edges(api, "man.client")
    state = parse(state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content)

    assert set(state.edges) == set(direct)


def test_edges_include_top_level_namespace_wiring(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """The Client → top-level namespace edge is present (commerce in nested.yaml)."""
    state = parse(state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content)

    edge = Edge(
        parent_module="client.py",
        factory_attr="__commerce_factory__",
        child_user_class="CommerceNamespace",
        child_user_module="namespaces/commerce.py",
    )
    assert edge in state.edges


def test_edges_include_namespace_to_collection_wiring(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """The Commerce → Orders collection edge is present."""
    state = parse(state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content)

    edge = Edge(
        parent_module="namespaces/commerce.py",
        factory_attr="__orders_factory__",
        child_user_class="OrdersCollection",
        child_user_module="collections/orders.py",
    )
    assert edge in state.edges


def test_edges_include_collection_to_resource_wiring(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """The Orders → Order resource edge uses `__resource_factory__`."""
    state = parse(state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content)

    edge = Edge(
        parent_module="collections/orders.py",
        factory_attr="__resource_factory__",
        child_user_class="OrderResource",
        child_user_module="resources/order.py",
    )
    assert edge in state.edges


def test_edges_include_resource_to_action_wiring(
    state_vfs: dict[str, GeneratedFile],
) -> None:
    """A resource-level action shows up as an edge from the resource."""
    state = parse(state_vfs[f"src/man/client/base/{STATE_FILENAME}"].content)

    edge = Edge(
        parent_module="resources/order.py",
        factory_attr="__submit_factory__",
        child_user_class="OrderSubmitAction",
        child_user_module="actions/order_submit.py",
    )
    assert edge in state.edges


def test_parse_round_trips_a_serialized_state() -> None:
    """`parse(serialize(s)) == s` so we can read back what we write."""
    from okapipy.generator.state import GeneratedState, serialize

    edges = [
        Edge(
            parent_module="client.py",
            factory_attr="__commerce_factory__",
            child_user_class="CommerceNamespace",
            child_user_module="namespaces/commerce.py",
        )
    ]
    state = GeneratedState(
        generator_version="0.x.y",
        generated_at="2026-04-30T00:00:00Z",
        base_files=["src/p/base/__init__.py"],
        edges=edges,
    )

    parsed = parse(serialize(state))

    assert parsed == state


def test_parse_tolerates_unknown_keys() -> None:
    """Adding a future field to the state file doesn't break older readers."""
    payload = json.dumps(
        {
            "generator_version": "999.0.0",
            "generated_at": "ts",
            "base_files": [],
            "edges": [],
            "future_field": "value",
        }
    )

    parse(payload)  # must not raise
