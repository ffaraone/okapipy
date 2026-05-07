# Parser internals

The parser turns a flat OpenAPI 3.x document into a hierarchical
`APIModel` tree. It runs as a linear pipeline, eight phases, each in its
own module. Reading the modules top-to-bottom in the order listed below
is the fastest way to understand the whole thing.

```
spec source
   │
   ▼
loader.py  ────►  raw_spec + spec   (prance, refs inlined; raw kept for $ref names)
   │
   ▼
nlp.py     ────►  spaCy pipeline    (POS tagging + plural/verb detection)
   │
   ▼
extension.py ───► spec extensions   (x-okapipy-ns, x-okapipy-kind, x-okapipy-exclude)
rules.py     ───► rules file        (project-local overrides; same shape, wins)
   │
   ▼
classifier.py ──► segment → kind    (resource | namespace | collection | action | singleton)
   │
   ▼
builder.py  ────► walks paths       (mutates APIModel in place; hooks operations into slots)
   │
   ▼
model.py    ────► APIModel          (Pydantic v2 tree)
   │
   ▼
dump.py     ────► JSON / YAML out   (parse-time visualization)
api.py      ────► public parse()    (the single supported entry point)
```

## Phase 1 — `loader.py`

`load_spec(source)` and `load_raw_spec(source)`. Both accept a local path
or http(s) URL, JSON or YAML — format auto-detected.

* `load_spec` runs prance with the `openapi-spec-validator` backend and
  inlines internal **and external** `$ref`s. Validation runs as part of
  the load.
* `load_raw_spec` uses `prance.BaseParser` to keep refs intact. The
  builder needs the un-resolved doc to recover original schema names by
  reading the trailing segment of each `$ref`.

`detect_base_path(spec)` reads the path component of the first
`servers[].url`. It does **not** auto-guess from path commonalities
(that heuristic stripped meaningful segments and was removed). If you
want a different prefix, pass `--strip-prefix` / `strip_prefix=`.

## Phase 2 — `nlp.py`

Two non-obvious tricks worth preserving:

### The "the X" wrapper for plural detection

Bare path segments like `tokens` or `users` get tagged `PROPN` with
`Number=Sing` by `en_core_web_sm` in isolation. Wrapping the segment in
a language-specific definite article (`PLURAL_CONTEXT`) coaxes the
tagger into a noun analysis with correct plurality. `lemma_in_context()`
uses the same trick for singularization (so `users` → `user`).

### Bare for verb detection, wrapper for plural

A clear verb in isolation (`reset`, `submit`) keeps its `VERB` tag; the
wrapper would force it to `NOUN`. So `_analyze_token` runs **both**
analyses (bare *and* wrapped) and combines the signals: bare tells us
"is this a verb?", wrapped tells us "if this is a noun, is it plural?".

### Model cache

Models live at `<cache_dir>/<package>/<package>-<version>/`.
`model_path()` resolves the inner versioned dir. Default `cache_dir` is
`Path.cwd() / ".spacy"`. A cache miss triggers
`python -m spacy download <model> --target <dir>` automatically (no
opt-in flag). The download is idempotent, so concurrent processes
trampling on the cache directory is fine.

## Phase 3 — `extension.py` + `rules.py`

These read the same kind of hint from two places:

* `extension.py` reads `x-okapipy-ns`, `x-okapipy-kind`,
  `x-okapipy-exclude`, `x-okapipy-paginated` from the spec.
* `rules.py` reads a project-local rules file (`Rules` / `PathRules` /
  `OperationRules` Pydantic models) that mirrors the same shape.

**Rules-file values win over spec on every conflict.** This is the
right precedence: the rules file exists precisely for the case where you
can't (or shouldn't) edit the upstream spec.

The rules file is **local-only** (no URL support). That's a deliberate
constraint; project-local overrides should be in your repo, not on a
remote server.

## Phase 4 — `classifier.py`

`classify_segment(...)` decides if a single segment is `NAMESPACE |
COLLECTION | RESOURCE_ID | ACTION | SINGLETON`. Precedence:

1. Path parameter (`{id}`) → `RESOURCE_ID`.
2. Explicit `x-okapipy-kind` (rules first, then spec).
3. Namespace registry (`x-okapipy-ns`).
4. spaCy POS / morphology:
   * Verb in isolation → `ACTION`.
   * Plural noun → `COLLECTION`.
   * Multi-word kebab segment whose head is **not** plural →
     verb-phrase action (e.g. `force-reimport`).
   * Single-token API verb in the language registry (`login`, `logout`,
     `refresh`, `revoke`, `verify`, `subscribe`, `unsubscribe`,
     `activate`, `deactivate`, `enable`, `disable`, `archive`, `publish`,
     `ping`, …) → `ACTION`.
5. Fallback → `COLLECTION` + warn.

A multi-word segment whose head is **not** plural is treated as a verb
phrase — that's how `force-reimport` becomes an action while
`password-recovery-requests` stays a collection.

## Phase 5 — `builder.py`

Walks paths and **mutates Pydantic models in place**. There are no
draft/wrapper types. Key invariants:

### Naming

`contextual_name(breadcrumb, current)` joins the **full** breadcrumb
(every singular collection name accumulated so far). So:

* `/organizations/{id}/datasources/{id}/force-reimport` →
  `OrganizationDatasourceForceReimport`.
* Resource names use `"".join(breadcrumb)` for the same reason.

Namespaces don't enter the breadcrumb — they're folders, not type
names. `/auth/login` is `Login`; `/users/{id}/avatar` is `UserAvatar`.

### Operation routing

| Terminal kind | GET | POST | PUT | PATCH | DELETE |
| --- | --- | --- | --- | --- | --- |
| Collection | `fetch` | `create` | dropped | dropped | dropped |
| Resource | `retrieve` | dropped | `update` | `partial_update` | `delete` |
| Singleton | `retrieve` | dropped | `update` | `partial_update` | `delete` |
| Action | appended to `Action.operations` (one Action per path holds every method on it) |

Anything that doesn't fit (e.g. `POST /users/{id}` with no
`x-okapipy-kind: action` hint, PUT on a bare collection) is **dropped
with a warning**, not coerced into a synthetic action. Synthetic
actions only exist for explicit `x-okapipy-kind: action` opt-ins.

### Forbidden combinations

**Namespace-level actions are forbidden** when the segment is itself a
namespace. An action segment under a `Namespace` raises
`InvalidStructureError`. (Actions *attached to* a namespace —
`/auth/refresh` — are fine; what's forbidden is treating a namespace
itself as an action.)

### Schema name recovery

`request_model` / `response_model` names are recovered from
`raw_spec` by reading the original `$ref`'s trailing segment. If a 2xx
response has no `$ref` (inline schema), the resolved schema's `title`
is used. If neither exists, the field is `None` — `Operation.response_model`
is `str | None` precisely because some 2xx responses have no body.

### Excludes

* `x-okapipy-exclude: "*"` skips a whole path.
* `x-okapipy-exclude: [DELETE, ...]` skips just those methods
  (case-insensitive).
* Rules-file values override spec values.

## Phase 6 — `model.py`

Pydantic v2 models. Two deliberate deviations from the spec in
`parser.md` §6:

* `APIModel` carries top-level `collections: list[Collection]`. Real
  APIs commonly expose `/orders` with no namespace prefix — forcing a
  fake namespace would distort the tree.
* `Operation.response_model` is `str | None` — some 2xx responses have
  no body (DELETE returning 204, for example).
* `Collection.fetch` and `Collection.create` are the slot names
  (renamed from the original `list_operation` / `create_operation`).
  Shorter, and `list` is a builtin you don't want to shadow.

## Phase 7 — `dump.py`

`write(api, path)` infers JSON vs YAML from `.json` / `.yaml` / `.yml`.
Anything else raises `ValueError`. There's also `to_json(api)` for the
"just give me a string" case used by the CLI.

## Phase 8 — `api.py`

The single public entry point:

```python
def parse(
    source: str | Path,
    rules: str | Path | None = None,
    lang: str = "en",
    *,
    strip_prefix: str | None = None,
    nlp_cache_dir: Path = DEFAULT_NLP_CACHE_DIR,
) -> APIModel: ...
```

`parse` returns `APIModel` directly — no result wrapper, no error tuple.
Non-fatal warnings go through `logging`; fatal problems raise a
`ParserError` subclass (caught by the CLI and surfaced as a non-zero
exit).

The full [API reference](../reference/parser.md) is auto-generated from
the docstrings.

## Adding a new heuristic

Most spec-shape oddities should be solvable with extensions or rules
without touching parser code. Reach for a parser change only when:

* The hint mechanism doesn't yet cover the case (rare — the four
  extensions cover essentially every classification override). In that
  case, add it in `extension.py` first, mirror it in `rules.py`, and
  thread it through `classifier.py` and `builder.py`.
* spaCy is reliably wrong on a class of segments. Look at `nlp.py`'s
  `VERB_REGISTRY` — that's the right place for "a known verb the small
  pipeline mistags as a noun." Anything fancier (custom matchers,
  domain-specific NER) is overkill for a CLI tool's startup budget.

When in doubt, add a fixture under `tests/fixtures/` exhibiting the
spec shape, write a test that *expects the wrong answer*, then push the
test green by changing the parser. The fixture stays in the suite
forever as a regression guard.
