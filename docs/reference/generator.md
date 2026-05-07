# Generator API reference

The generator package exposes one supported entry point —
`generate(...)` — that returns a virtual filesystem
(`dict[str, GeneratedFile]`). The CLI flushes that dict to disk via
`write_to_disk`; tests inspect it directly.

## Public entry point

::: okapipy.generator.api.generate

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

## Manifest and edges

::: okapipy.generator.manifest
    options:
      members:
        - serialize
        - MANIFEST_FILENAME

::: okapipy.generator.edges
    options:
      members:
        - compute_manifest

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
