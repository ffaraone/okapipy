<p align="center">
  <img src="https://raw.githubusercontent.com/ffaraone/okapipy/main/assets/logo.png" alt="okapipy" width="220" />
</p>

<h1 align="center">okapipy</h1>

[![CI](https://github.com/ffaraone/okapipy/actions/workflows/ci.yml/badge.svg)](https://github.com/ffaraone/okapipy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/okapipy.svg)](https://pypi.org/project/okapipy/)
[![Python versions](https://img.shields.io/pypi/pyversions/okapipy)](https://pypi.org/project/okapipy/)
[![License](https://img.shields.io/pypi/l/okapipy)](https://github.com/ffaraone/okapipy/blob/main/LICENSE)

[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=ffaraone_okapipy)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=coverage)](https://sonarcloud.io/component_measures?id=ffaraone_okapipy&metric=coverage)
[![Maintainability](https://sonarcloud.io/api/project_badges/measure?project=ffaraone_okapipy&metric=sqale_rating)](https://sonarcloud.io/component_measures?id=ffaraone_okapipy&metric=sqale_rating)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Checked with mypy](https://img.shields.io/badge/mypy-checked-blue.svg)](http://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A Python OpenAPI client generator that lifts the flat list of paths in an
OpenAPI 3.x document into a hierarchical tree of `Namespace → Collection →
Resource → (Sub-Collection | Action)` and emits a strongly-typed,
async/sync Pydantic v2 client from it.

## Installation

okapipy requires Python 3.12+ and uses [uv](https://docs.astral.sh/uv/) for
dependency management.

```bash
uv add okapipy            # add to an existing project
# or, for one-off use:
uvx okapipy --help
```

The first NLP-dependent run downloads the spaCy `en_core_web_sm` model
(~12 MB) into `./.spacy/`. To pre-warm it:

```bash
uv run okapipy nlp fetch en
```

## Usage

Parse a spec into its structural tree (path or http(s) URL accepted):

```bash
uv run okapipy spec parse openapi.yaml --output tree.yaml
```

Generate a full client project:

```bash
uv run okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient
```

This writes a complete Python project under `./my-client` with a
regeneratable base layer (`src/acme/commerce/base/...`) and a one-shot user
layer of subclass stubs you can safely customize. Re-running the command
refreshes the base layer while preserving your edits in the user layer.

Useful flags:

- `--rules path/to/rules.yaml` — project-local overrides for namespace
  assignment, segment kind, and operation exclusion (mirrors the
  `x-okapipy-*` extensions; rules-file values win on conflict).
- `--strip-prefix /api/v1` — drop a base prefix from every path before
  classification.
- `--check` — CI dry-run: report drift and stale files, exit non-zero on
  any change.
