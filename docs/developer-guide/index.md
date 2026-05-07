# Developer Guide

This guide is for **contributors to okapipy** (and for anyone who wants to
understand what's going on under the hood). If you're trying to *use*
okapipy to build a client, the [User Guide](../user-guide/installation.md)
is the right place.

## Getting set up

okapipy is managed with [uv][uv]. Python is pinned to **3.13** via
`.python-version` because spaCy doesn't yet ship 3.14 wheels.

```bash
git clone https://github.com/ffaraone/okapipy
cd okapipy
uv sync
```

That installs the runtime dependencies plus the `dev` group (mypy, ruff,
pytest, pytest-cov, pytest-mock, pytest-httpserver, etc.).

The first NLP-dependent test run will download the spaCy
`en_core_web_sm` model (~12 MB) into `./.spacy/`. Subsequent runs are
offline. To pre-warm:

```bash
uv run okapipy nlp fetch en
```

## Common commands

```bash
uv sync                                    # install deps (incl. dev group)

# Tests
uv run pytest                              # full suite + coverage
uv run pytest tests/parser/test_X.py       # single file
uv run pytest -k "name_of_test"            # single test by substring
uv run pytest --no-cov                     # faster: skip coverage

# Type-check (strict on parser + generator only)
uv run mypy src tests

# Lint + format check
uv run ruff check src tests
uv run ruff format --check src tests

# CLI smoke
uv run okapipy --help
uv run okapipy spec parse tests/fixtures/<some>.yaml
```

`pyproject.toml` configures `--cov=okapipy` so plain `pytest` always emits
a coverage report; `htmlcov/` and `coverage.xml` are written. The
**parser package** has a 90 % coverage minimum (currently sits ~94 %).

## Project layout

```
src/okapipy/
├── app.py            # entry point (typer)
├── parser/           # spec → APIModel tree (the "lift")
│   ├── api.py        # public parse(...) entry
│   ├── loader.py     # JSON/YAML loader, $ref inlining, base path detect
│   ├── nlp.py        # spaCy pipeline, fetch_model, plural/verb detection
│   ├── extension.py  # x-okapipy-* readers
│   ├── rules.py      # Pydantic models for the rules file
│   ├── classifier.py # segment → kind decision logic
│   ├── builder.py    # walks paths, mutates APIModel in place
│   ├── model.py      # the APIModel / Namespace / Collection / ... Pydantic types
│   ├── dump.py       # write APIModel to JSON / YAML
│   └── errors.py     # ParserError hierarchy
├── generator/        # APIModel → Python project (the "emit")
│   ├── api.py        # public generate(...) entry
│   ├── emit/         # one module per output surface (client, project, runtime, …)
│   ├── runtime/      # runtime helpers vendored into every generated client
│   ├── templates/    # Jinja templates for the generated code
│   ├── models.py     # datamodel-code-generator integration
│   ├── manifest.py   # _manifest.json (drift detection)
│   ├── edges.py      # parent-child wiring metadata
│   ├── inline_schemas.py  # schema name recovery from $refs
│   ├── templating.py # ChoiceLoader: user templates → packaged templates
│   ├── vfs.py        # virtual filesystem + write_to_disk
│   └── errors.py     # GenerationError hierarchy
└── cli/              # typer subcommands
    ├── __init__.py   # `okapipy` root, dispatches to nlp / spec
    ├── nlp_cmd.py    # `okapipy nlp ...`
    ├── spec_cmd.py   # `okapipy spec ...`
    └── console.py    # rich-based stdout/stderr helpers
```

The split is intentional: the parser is a **pure function** from
(spec, rules) → tree. The generator is a **pure function** from
tree → virtual filesystem. The CLI glues them together, but anything you
can do from the CLI you can do programmatically by calling `parse(...)`
followed by `generate(...)`.

## Where to dig next

* [Parser internals](parser.md) — the eight-phase pipeline, the
  classifier's precedence rules, and the NLP tricks (the "the X" wrapper,
  the verb registry).
* [Generator internals](generator.md) — how the parser tree becomes a
  multi-file Python project, the base-vs-user lifecycle, the manifest +
  drift detection, and the templating layer.

## House style

Conventions that are easier to follow if you know they're deliberate:

* **No underscore-prefixed "private" functions.** Module-internal helpers
  that aren't used outside the source file are fine; otherwise functions
  are public.
* **No import aliases** unless strictly necessary.
* **All imports stay at the top** of the file. Local imports inside
  functions are reserved for genuine cyclic-import or
  lazy-initialization cases.
* **`from __future__ import annotations` everywhere.** Type hints are
  mandatory in the parser; the generator follows suit.
* **`mypy --strict`** is enforced for `okapipy.parser.*` and
  `okapipy.generator.*`. Tests are looser.
* **Pydantic v2.** No v1-style configs.
* **Google-style docstrings** on every public function. They drive the
  auto-generated [Reference](../reference/cli.md) section of these docs.

## Tests

* **pytest functions only**, no `unittest.TestCase` classes.
* **Every test has a docstring** explaining *what* it's verifying — not
  just restating the function name.
* **Fixtures live in `tests/conftest.py`**, not inline in test files. The
  `english_nlp` session-scoped fixture loads the spaCy pipeline; an
  autouse fixture clears the in-process cache between tests so loader
  paths stay observable.
* **Mocking uses `pytest_mock`** (`mocker` fixture). No
  `from unittest.mock import ...`.
* **OpenAPI fixtures** live under `tests/fixtures/`. `pytest-httpserver`
  serves them over HTTP for URL-source tests.

## Releasing

A new release is cut by **publishing a GitHub release** with a `v<x.y.z>`
tag. The `release.yml` workflow runs lint + type-check + tests, then
builds and publishes the sdist + wheel to PyPI via trusted publishing
(no token in the repo).

The same release event triggers the docs deployment (see
`.github/workflows/docs.yml`), which builds these docs with mkdocs and
pushes them to the `gh-pages` branch.

[uv]: https://docs.astral.sh/uv/
