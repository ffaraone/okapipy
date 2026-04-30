# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

okapipy turns an OpenAPI 3.x spec into a strongly-typed Python client by first lifting the flat list of paths into a **hierarchical structural tree** of `Namespace → Collection → Resource → (Sub-Collection | Action)`. Today the repo ships **the parser only**; the code generator that consumes the tree is not yet implemented (`generator.md` is a placeholder).

The design rationale and node taxonomy live in `parser.md` (canonical spec) and `parser_plan.md` (implementation plan + decided open questions). Read these before changing parser behavior — they explain why the tree shape is what it is.

## Common commands

Dependencies are managed with `uv`. Python is pinned to 3.13 via `.python-version` because spaCy lacks 3.14 wheels. The first NLP-dependent test run will download `en_core_web_sm` (~12 MB) into `./.spacy/`; subsequent runs are offline.

```bash
uv sync                                  # install deps (incl. dev group)
uv run pytest                            # full suite + coverage report
uv run pytest tests/parser/test_X.py     # single file
uv run pytest -k "name_of_test"          # single test by substring
uv run pytest --no-cov                   # faster: skip coverage
uv run mypy src/okapipy/parser           # strict type-check (gated on parser only)
uv run ruff check src tests              # lint
uv run okapipy nlp fetch en              # pre-download spaCy model into ./.spacy
uv run okapipy spec parse <SOURCE>       # parse a spec; SOURCE is a path or http(s) URL
```

`pyproject.toml` configures `--cov=okapipy` so plain `uv run pytest` always emits coverage; `htmlcov/` and `coverage.xml` are written. `[tool.mypy]` runs strict mode **only** for `okapipy.parser.*` — keep new parser code passing strict.

## Parser pipeline (read this before editing parser code)

The parser is a linear pipeline. Each phase lives in one module under `src/okapipy/parser/`:

1. **`loader.py`** — `load_spec(source)` and `load_raw_spec(source)`. Both accept a local path or http(s) URL and JSON or YAML; format auto-detected. `load_spec` runs prance with the `openapi-spec-validator` backend and inlines internal **and external** `$ref`s. `load_raw_spec` uses `prance.BaseParser` to keep refs intact — needed because the builder recovers original schema names from the un-resolved doc. `detect_base_path(spec)` reads the path component of the first `servers[].url`; it does **not** auto-guess from path commonalities (that heuristic stripped meaningful segments and was removed).

2. **`nlp.py`** — spaCy-backed POS/morphology. Two non-obvious tricks worth preserving:
   - **`"the X"` wrapper for plural detection.** Bare path segments like `tokens` or `users` get tagged `PROPN` with `Number=Sing` by `en_core_web_sm` in isolation. Wrapping in a language-specific definite article (`PLURAL_CONTEXT`) coaxes the tagger into a noun analysis with correct plurality. `lemma_in_context()` uses the same trick for singularization.
   - **Bare for verb detection, wrapper for plural.** A clear verb in isolation (`reset`, `submit`) keeps its `VERB` tag; the wrapper would force it to `NOUN`. So `_analyze_token` runs both and combines the signals.
   - Models live at `<cache_dir>/<package>/<package>-<version>/`. `model_path()` resolves the inner versioned dir. Default `cache_dir` is `Path.cwd() / ".spacy"`. Cache miss triggers `python -m spacy download <model> --target <dir>` automatically (no opt-in flag).

3. **`extension.py`** + **`rules.py`** — read `x-okapipy-ns`, `x-okapipy`, `x-okapipy-exclude` from the spec and from a project-local rules file (`Rules` / `PathRules` / `OperationRules` Pydantic models) that mirrors the same shape. **Rules-file values win over spec** on every conflict. The rules file is a *local file only* (no URL support).

4. **`classifier.py`** — `classify_segment` decides if a single segment is `NAMESPACE | COLLECTION | RESOURCE_ID | ACTION`. Precedence: path-parameter → explicit `x-okapipy` hint → namespace registry → spaCy → fallback (`COLLECTION` + warn). A multi-word segment whose head is **not** plural is treated as a verb-phrase action — that's how `force-reimport` becomes an action while `password-recovery-requests` stays a collection.

5. **`builder.py`** — walks paths and **mutates Pydantic models in place**. There are no draft/wrapper types. Key invariants:
   - `contextual_name(breadcrumb, current)` joins the **full** breadcrumb (every singular collection name accumulated so far), so `/organizations/{id}/datasources/{id}/force-reimport` becomes `OrganizationDatasourceForceReimport`.
   - Resource names use `"".join(breadcrumb)` for the same reason.
   - Operation routing: GET/POST on `Collection` → `fetch`/`create`; GET/PUT/PATCH/DELETE on `Resource` → `retrieve`/`update`/`partial_update`/`delete`. Anything that doesn't fit (e.g. `POST /users/{id}` with no `x-okapipy: action` hint, PUT on a bare collection) is **dropped with a warning**, not coerced into a synthetic action. Synthetic actions exist only for explicit `x-okapipy: action` opt-ins.
   - **Namespace-level actions are forbidden**: an action segment under a `Namespace` raises `InvalidStructureError`.
   - Schema names for `request_model` / `response_model` are recovered from `raw_spec` by reading the original `$ref`'s trailing segment; falls back to the resolved schema's `title` if no ref.
   - `x-okapipy-exclude: "*"` skips a whole path; `x-okapipy-exclude: [DELETE, ...]` (case-insensitive) skips just those methods. Rules-file values override spec values.

6. **`model.py`** — Pydantic v2 models from `parser.md` §6, **with two deliberate deviations**: `APIModel` carries top-level `collections: list[Collection]` (real APIs commonly expose `/orders` with no namespace prefix), and `Operation.response_model` is `str | None` (some 2xx responses have no body). `Collection.fetch` and `.create` are the slot names (renamed from the original `list_operation`/`create_operation`).

7. **`dump.py`** — `write(api, path)` infers JSON vs YAML from `.json`/`.yaml`/`.yml` extension. Unknown extension → `ValueError`.

8. **`api.py`** — `parse(source, rules=None, lang="en", *, strip_prefix=None, nlp_cache_dir=cwd/.spacy)` is the single public entry. `rules` is an optional path to a `Rules` JSON/YAML file. Returns `APIModel` directly (no result wrapper) — non-fatal warnings go to `logging`.

## CLI

`pyproject.toml` wires `okapipy = "okapipy.app:main"` → `okapipy/cli/__init__.py` (typer). Two sub-apps:
- `okapipy nlp fetch <LANG> [--cache-dir]` — pre-warm the spaCy model.
- `okapipy spec parse <SOURCE> [--rules] [--lang] [--nlp-cache-dir] [--output]` — parse + optionally write to a file (format inferred from extension).

`SOURCE` accepts both file paths and URLs. Errors raise `ParserError` subclasses; the CLI catches them, prints to stderr, and exits non-zero.

## Test conventions

These come from `parser_plan.md` §16 and prior feedback — keep them consistent with existing tests:
- **pytest functions only**, no unittest classes. Every test has a docstring explaining *what* it's verifying (not just restating the function name).
- **Fixtures live in `tests/conftest.py`**, not inline in test files. The `english_nlp` session-scoped fixture loads the spaCy pipeline from `./.spacy/`; an autouse `_reset_nlp_cache` clears the in-process cache between tests so loader paths stay observable.
- **Mocking uses `pytest_mock`** (`mocker` fixture). No `from unittest.mock import ...`.
- **Coverage minimum is 90%** for the parser package; the suite currently sits at ~94%.
- **Docstrings are self-contained** — no "see file X" or "described in §Y of doc Z". Each docstring stands alone.

OpenAPI fixtures live under `tests/fixtures/`. `pytest-httpserver` (`served_fixtures` fixture) serves them over HTTP for URL-source tests.

## Style

- `ruff` with `max-line-length = 100`; isort known-first-party = `okapipy`.
- **No underscore-prefixed "private" functions** in the parser per `parser.md` §9 — module organization handles encapsulation. Genuinely module-internal helpers (e.g. `_attach`, `_route`) are an exception within `builder.py`.
- **No import aliases** unless strictly necessary. The two existing `from … import X as Y` aliases in `builder.py` exist because both `extension` and `rules` export a same-named exclusion helper.
- `from __future__ import annotations` everywhere; type hints are mandatory for all parser code.
