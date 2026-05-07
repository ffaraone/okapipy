# Parser API reference

The parser package exposes one supported entry point — `parse(...)` —
plus the Pydantic models that describe the parsed tree. Anything else
listed here is a documented internal you may import if you need to
build custom tooling on top.

## Public entry point

::: okapipy.parser.api.parse

## The data model

The tree returned by `parse(...)` is a graph of these Pydantic v2
models. They're immutable in spirit (downstream code generators treat
them as read-only) and round-trip cleanly through JSON / YAML.

::: okapipy.parser.model.APIModel
::: okapipy.parser.model.Namespace
::: okapipy.parser.model.Collection
::: okapipy.parser.model.Resource
::: okapipy.parser.model.Singleton
::: okapipy.parser.model.Action
::: okapipy.parser.model.Operation

## Loading specs and rules

::: okapipy.parser.loader
    options:
      members:
        - load_spec
        - load_raw_spec
        - detect_base_path

::: okapipy.parser.rules
    options:
      members:
        - load_rules
        - Rules
        - PathRules
        - OperationRules

## NLP

::: okapipy.parser.nlp
    options:
      members:
        - load_pipeline
        - fetch_model
        - model_path
        - DEFAULT_CACHE_DIR

## Classifier and builder

The classifier and builder are the heart of the pipeline. You generally
won't call them directly — use `parse(...)` — but their docstrings are
the most precise statement of what each phase does.

::: okapipy.parser.classifier
    options:
      members:
        - classify_segment

::: okapipy.parser.builder
    options:
      members:
        - build
        - contextual_name

## Dumping the tree

::: okapipy.parser.dump
    options:
      members:
        - write
        - to_json

## Errors

::: okapipy.parser.errors
    options:
      show_root_heading: false
      show_if_no_docstring: true
