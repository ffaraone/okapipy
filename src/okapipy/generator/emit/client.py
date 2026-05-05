"""Render the templated `client.py` (sync + async classes) into the virtual FS."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jinja2 import Environment

from okapipy.generator.emit.walk import (
    action_attr,
    action_class,
    action_module,
    collection_attr,
    collection_class,
    collection_module,
    collection_property_docstring,
    factory_attr,
    namespace_class,
    namespace_module,
    singleton_attr,
    singleton_class,
    singleton_module,
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

    The client class wires up properties for top-level namespaces and collections
    so users can write `client.commerce.orders` / `client.orders` immediately.
    """
    top_namespaces = [
        {
            "attr": snake_case(ns.name),
            "class_name": namespace_class(ns),
            "module": namespace_module(ns),
            "factory_attr": factory_attr(snake_case(ns.name)),
        }
        for ns in api.namespaces
    ]
    top_collections = [
        {
            "attr": collection_attr(coll),
            "class_name": collection_class(coll),
            "module": collection_module(coll),
            "factory_attr": factory_attr(collection_attr(coll)),
            "docstring": collection_property_docstring(coll),
        }
        for coll in api.collections
    ]
    top_singletons = [
        {
            "attr": singleton_attr(sing),
            "class_name": singleton_class(sing),
            "module": singleton_module(sing),
            "factory_attr": factory_attr(singleton_attr(sing)),
        }
        for sing in api.singletons
    ]
    top_actions = [
        {
            "attr": action_attr(action),
            "class_name": action_class(action),
            "module": action_module(action),
            "factory_attr": factory_attr(action_attr(action)),
        }
        for action in api.actions
    ]
    ctx = {
        **project_context,
        "top_namespaces": top_namespaces,
        "top_collections": top_collections,
        "top_singletons": top_singletons,
        "top_actions": top_actions,
    }
    return {
        f"src/{package_path}/base/client.py": render_python(
            env, "package/client.py.jinja", ctx
        ),
    }
