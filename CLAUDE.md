# okapipy Development Guidelines

## Project

okapipy turns an OpenAPI 3.x document into a typed Python client. It runs in
two stages:

1. **Parser** (`src/okapipy/parser/`) — lifts the flat list of paths into a
   hierarchical structural tree of `Namespace → Collection → Resource →
   (Sub-Collection | Sub-Singleton | Action)`, with `Singleton` and `Action`
   also legal at the root or directly under a namespace.
2. **Generator** (`src/okapipy/generator/`) — walks the parsed `APIModel`
   and emits a runnable Python project: a regenerated `base/` layer
   (HTTP transport, Pydantic models, vendored runtime, tree wiring) and a
   one-shot user layer of subclass stubs the customer is free to edit.
   Sync and async clients are siblings, both produced from the same
   templates.

The two-layer split is what makes regeneration lossless: re-running the
generator after a spec change rewrites `base/` and leaves the user layer
strictly alone.

## Requirements and design

Requirements and design decisions are **not** maintained in this file. They
live in two external documents and are the source of truth a maintainer
must honor:

* [`design/REQUIREMENTS.md`](design/REQUIREMENTS.md) — what the code must do.
* [`design/DESIGN.md`](design/DESIGN.md) — how the code does it (modules,
  data flow, key invariants).

Read the relevant section of both before changing parser, generator, or
customization behavior. When you change behavior, update both files —
drift between code and either file is a bug.

The user-facing documentation site (mkdocs, rendered to
<https://ffaraone.github.io/okapipy/>) lives in [`docs/`](docs/) and is a
separate concern: `design/` describes the contract a maintainer must honor;
`docs/` describes the experience an end user gets.

## Project structure

```
.
├── src/okapipy/
│   ├── parser/         # Stage 1: OpenAPI spec → structural APIModel tree
│   ├── generator/      # Stage 2: APIModel → generated client project
│   │   ├── emit/       # Per-artifact emitters (project, runtime, client, walk, stubs, tests)
│   │   ├── runtime/    # Vendored library copied verbatim into every generated package
│   │   └── templates/  # Default Jinja templates (overridable via --templates-dir)
│   ├── cli/            # typer entry points (okapipy nlp …, okapipy spec …)
│   └── app.py          # CLI bootstrap (okapipy = okapipy.app:main)
├── tests/
│   ├── parser/         # Parser unit tests
│   ├── generator/      # Generator unit + integration tests
│   ├── fixtures/       # OpenAPI fixtures (served via pytest-httpserver where needed)
│   └── conftest.py     # Shared fixtures (no inline fixtures in test files)
├── design/             # Maintainer-facing contract (REQUIREMENTS.md, DESIGN.md)
├── docs/               # User-facing mkdocs site
├── pyproject.toml      # Tooling, dependencies, ruff/mypy/pytest config
└── .python-version     # Pinned to 3.13 (spaCy lacks 3.14 wheels)
```

Dependencies are managed with `uv`. The first NLP-dependent run downloads
`en_core_web_sm` (~12 MB) into `./.spacy/`; subsequent runs are offline.

## Mandatory checks on every change

Every change MUST pass the following checks before being proposed for
merge. They run locally and in CI; do not bypass them.

```bash
uv run pytest                             # full suite + coverage report
uv run prek run --all-files               # all pre-commit hooks (ruff, mypy, etc.)
uv run mkdocs build --strict              # docs build with no warnings
```

In addition, every change MUST regenerate a sample client project and the
checks on the generated project MUST pass:

```bash
uv run okapipy spec generate <SOURCE> --output ./out --package acme.demo --client-class DemoClient
cd ./out && uv run pytest                 # generated test suite
cd ./out && uv run ruff check .           # lint rules + isort
cd ./out && uv run ruff format --check .  # formatting
```

If any of the above fails, fix the root cause — do not silence checks or
disable hooks.

## Per-change workflow

For every change you MUST:

1. **Change the code.** One logical change at a time.
2. **Add or fix tests.** New behavior needs new tests; bug fixes need a
   regression test that fails without the fix. Use the `python-testing`
   skill to write tests.
3. **Update design documents** (`design/REQUIREMENTS.md`,
   `design/DESIGN.md`) when the contract changes. Requirements drift
   first, design drift follows.
4. **Update the user-facing documentation** (`docs/`, README) when user
   experience changes (CLI flags, generated client surface, new
   strategies/templates). Use the `technical-writer` skill to manage
   user-facing documentation.

## Branches and commits

* `main` is **protected**. No direct pushes — every change lands via PR.
* Branch names use the format `<type>/<slug>`, where `<type>` is one of
  `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.
* Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/):
  `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `build:`,
  `ci:`.
* Subject line in **imperative mood**, ≤72 characters. The body explains
  **why**, not how.
* **One logical change per commit.** Rebase to clean up before opening a
  PR; do not merge `main` into the branch.

## GitHub CLI usage

The `gh` CLI is allowed for **read-only** operations only (e.g.
`gh pr view`, `gh pr list`, `gh run list`, `gh issue view`). Any write
operation — creating PRs, commenting, merging, closing issues, editing
labels, pushing — requires the user to explicitly request it.

## Third-party dependencies

If a change requires a new third-party open-source library:

1. The library MUST be **actively maintained** (recent releases, responsive
   to issues, healthy community).
2. The license MUST be **OSI-approved, popular and have a strong
   community** — see
   <https://opensource.org/licenses?categories=popular-strong-community>.
   Anything outside that list requires explicit approval before adoption.

## Coding rules

### 0. Follow PEP 20 (The Zen of Python)

* Beautiful is better than ugly.
* Explicit is better than implicit.
* Simple is better than complex.
* Complex is better than complicated.
* Flat is better than nested.
* Sparse is better than dense.
* Readability counts.
* Special cases aren't special enough to break the rules.
* Although practicality beats purity.
* Errors should never pass silently.
* Unless explicitly silenced.
* In the face of ambiguity, refuse the temptation to guess.
* There should be one-- and preferably only one --obvious way to do it.
* Although that way may not be obvious at first unless you're Dutch.
* Now is better than never.
* Although never is often better than *right* now.
* If the implementation is hard to explain, it's a bad idea.
* If the implementation is easy to explain, it may be a good idea.
* Namespaces are one honking great idea -- let's do more of those!

### 1. Imports

* All imports stay at the **top of the file**. No inline imports inside
  functions.

### 2. Type annotations

* Type annotations are **always mandatory** — on every function, method,
  parameter, and return value.

### 3. Circular imports

* To avoid circular imports, **always refactor first**. Lazy imports are
  not an acceptable shortcut.

### 4. Function ordering

* The **public interface** of a module comes first; private functions
  follow.

### 5. Private functions

* Underscore-prefixed functions are allowed **only when used within the
  same module**. Importing an underscore-prefixed symbol from another
  module is **prohibited**.

### 6. Method ordering

* The **public interface** of a class comes first; private methods follow.

### 7. Constants

* Constant values stay **immediately after the imports** at the top of the
  module.

### 8. Import aliases

* Avoid import aliases unless **strictly necessary** (e.g. resolving a
  name clash).

### 9. Comments

* Code should be **self-explanatory**. Add comments only when strictly
  needed to surface a non-obvious *why*: a hidden constraint, a subtle
  invariant, or a workaround.

## Docstring rules

1. Every public function, class, and module gets a docstring. **Google
   style** for parser code; a one-paragraph summary for everything else
   is fine.
2. **Docstrings are self-contained.** Do not write "see file X" or
   "described in §Y of doc Z". A reader landing on the symbol from an
   IDE should understand it without leaving the file.
3. Document **what** the function returns and **why** it might raise —
   not the obvious **how**.
4. Surface **non-obvious invariants** (e.g. "rules-file values win over
   spec values") in the docstring of the function that enforces them.

## Skills

* Use the **`python-testing`** skill whenever writing, refactoring, or
  debugging tests.
* Use the **`technical-writer`** skill whenever writing or editing the
  user-facing documentation under `docs/` or the README.
