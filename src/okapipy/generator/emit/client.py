"""Render the templated `client.py` (sync + async classes) into the virtual FS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jinja2 import Environment

from okapipy.generator.emit.walk import (
    ChildRef,
    action_accessor_docstring,
    action_attr,
    action_class,
    action_meta_inline,
    action_module,
    action_one_line,
    build_client_class_docstring,
    collection_attr,
    collection_class,
    collection_module,
    collection_one_line,
    collection_property_docstring,
    factory_attr,
    namespace_accessor_docstring,
    namespace_class,
    namespace_module,
    node_one_line,
    singleton_accessor_docstring,
    singleton_attr,
    singleton_class,
    singleton_module,
    singleton_one_line,
)
from okapipy.generator.templating import render_python, snake_case
from okapipy.parser.model import APIModel


def emit_client(
    env: Environment,
    project_context: Mapping[str, Any],
    package_path: str,
    api: APIModel,
) -> dict[str, str]:
    """Render `client.py` (sync + async classes) and return `{path: content}`.

    The client class wires up properties for top-level namespaces, collections,
    singletons, and actions so users can write `client.commerce.orders` /
    `client.orders` / `client.me` immediately. Each accessor carries an
    IDE-friendly docstring; the class docstring lists every reachable child
    so a hover on the client class reads as a top-of-tree map.
    """
    top_namespaces = [
        ChildRef(
            attr=snake_case(ns.name),
            class_name=namespace_class(ns),
            module=namespace_module(ns),
            factory_attr=factory_attr(snake_case(ns.name)),
            docstring=namespace_accessor_docstring(ns),
            one_line=node_one_line(
                ns.summary,
                ns.description,
                fallback=f"Namespace `{ns.name}`.",
            ),
        )
        for ns in api.namespaces
    ]
    top_collections = [
        ChildRef(
            attr=collection_attr(coll),
            class_name=collection_class(coll),
            module=collection_module(coll),
            factory_attr=factory_attr(collection_attr(coll)),
            docstring=collection_property_docstring(coll),
            one_line=collection_one_line(coll),
        )
        for coll in api.collections
    ]
    top_singletons = [
        ChildRef(
            attr=singleton_attr(sing),
            class_name=singleton_class(sing),
            module=singleton_module(sing),
            factory_attr=factory_attr(singleton_attr(sing)),
            docstring=singleton_accessor_docstring(sing),
            one_line=singleton_one_line(sing),
        )
        for sing in api.singletons
    ]
    top_actions = [
        ChildRef(
            attr=action_attr(action),
            class_name=action_class(action),
            module=action_module(action),
            factory_attr=factory_attr(action_attr(action)),
            docstring=action_accessor_docstring(action),
            one_line=action_one_line(action),
            meta_inline=action_meta_inline(action),
        )
        for action in api.actions
    ]
    project_name = str(project_context.get("project_name", ""))
    project_version = project_context.get("project_version")
    sync_class_docstring = build_client_class_docstring(
        project_name=project_name,
        project_version=project_version if isinstance(project_version, str) else None,
        sync=True,
        top_namespaces=top_namespaces,
        top_collections=top_collections,
        top_singletons=top_singletons,
        top_actions=top_actions,
    )
    async_class_docstring = build_client_class_docstring(
        project_name=project_name,
        project_version=project_version if isinstance(project_version, str) else None,
        sync=False,
        top_namespaces=top_namespaces,
        top_collections=top_collections,
        top_singletons=top_singletons,
        top_actions=top_actions,
    )
    ctx = {
        **project_context,
        "top_namespaces": top_namespaces,
        "top_collections": top_collections,
        "top_singletons": top_singletons,
        "top_actions": top_actions,
        "client_class_docstring": sync_class_docstring,
        "async_client_class_docstring": async_class_docstring,
    }
    return {
        f"src/{package_path}/base/client.py": render_python(
            env, "package/client.py.jinja", ctx
        ),
    }
