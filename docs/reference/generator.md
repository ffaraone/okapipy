# Generator API reference

The generator package exposes two supported entry points:

* `generate(manifest)` — the manifest-driven public API that powers
  `okapipy generate`.
* `generate_for_mount(api, raw_spec, ...)` — the per-mount building
  block used by `generate` and exposed for tests and embedded callers.

Both return a virtual filesystem (`dict[str, GeneratedFile]`). The CLI
flushes the dict to disk via `write_to_disk`; tests inspect it
directly.

## Project manifest

The user-authored project manifest lives in
[`okapipy.manifest`](../user-guide/quick-start.md#the-project-manifest).

::: okapipy.manifest
    options:
      members:
        - GenerationManifest
        - SpecEntry
        - load_manifest
        - apply_cli_overrides
        - DEFAULT_MANIFEST_FILENAME

## Public entry points

::: okapipy.generator.api.generate

::: okapipy.generator.api.generate_for_mount

## The virtual filesystem

::: okapipy.generator.vfs
    options:
      members:
        - GeneratedFile
        - WriteReport
        - write_to_disk

## Models

::: okapipy.generator.models
    options:
      members:
        - emit_models
        - public_names

## Generated-state file and edges

::: okapipy.generator.state
    options:
      members:
        - GeneratedState
        - Edge
        - serialize
        - STATE_FILENAME

::: okapipy.generator.edges
    options:
      members:
        - compute_state
        - compute_edges

## Mount composition

::: okapipy.generator.compose
    options:
      members:
        - MountedSpec
        - mount_segments
        - mount_relpath
        - check_mount_collisions
        - iter_mount_namespace_prefixes

## Templating

::: okapipy.generator.templating
    options:
      members:
        - make_environment

## Errors

::: okapipy.generator.errors
    options:
      show_root_heading: false
      show_if_no_docstring: true

## Generated runtime

These modules are **vendored into every generated client** as flat
files directly under `<package>/base/` (`strategies.py`, `filters.py`,
`sort.py`, `transport.py`, `exceptions.py`, `types.py`). They're
documented here because you'll import them in your user layer for
custom strategies, custom filters, and custom transports — but you
don't import them from the `okapipy` package itself at runtime.

### Strategies

::: okapipy.generator.runtime.strategies
    options:
      members:
        - PaginationStrategy
        - FilterStrategy
        - SortStrategy
        - FilterEncoding
        - SortEncoding
        - LimitOffsetPagination
        - PageNumberPagination
        - CursorPagination
        - LinkHeaderPagination
        - KeyValueFilter
        - KeyOpValueFilter
        - SearchFilterStrategy
        - JsonFilterStrategy
        - CommaSignedSort
        - KeyDirectionSort
        - JsonApiSort

### Filter and sort DSL

::: okapipy.generator.runtime.filters
    options:
      members:
        - Filter
        - AndFilter
        - OrFilter
        - NotFilter
        - Search

::: okapipy.generator.runtime.sort
    options:
      members:
        - Sort

### Transport

::: okapipy.generator.runtime.transport

### Runtime exceptions

::: okapipy.generator.runtime.exceptions
    options:
      show_root_heading: false
      show_if_no_docstring: true
