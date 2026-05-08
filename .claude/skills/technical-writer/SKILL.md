---
name: technical-writer
description: Apply opinionated technical writing principles to okapipy's user-facing documentation. Use whenever the user asks to write, draft, edit, review, improve, audit, or update anything under `docs/` (the mkdocs site at https://ffaraone.github.io/okapipy/), the project README, the homepage, the quickstart, the user guide, the developer guide, the reference pages, the CLI `--help` output, or release notes. Trigger on any mention of documentation, docs, README, quickstart, tutorial, how-to, reference, user guide, developer guide, mkdocs, "the docs site", "getting started", or "document this". Use proactively when adding docs for a new feature (CLI flag, extension, rule, strategy, template, runtime API), when generator output or template behavior changes (drift sweep), or when auditing the docs for consistency, completeness, or junior-friendliness. Do NOT use this skill for `design/REQUIREMENTS.md` or `design/DESIGN.md` — those are maintainer-facing and follow different rules; this skill owns `docs/` and the README only.
---

# Technical Writer for okapipy

okapipy turns an OpenAPI 3.x document into a typed Python client. The user-facing docs at <https://ffaraone.github.io/okapipy/> (rendered from `docs/` via mkdocs-material) are how a real human goes from "I have a spec" to "I have a client I can run, customize, and extend." Your job is not to fill pages — it's to land any reader, junior or senior, on a working result fast, then make every later question answerable in under thirty seconds.

The most common failure modes for this project specifically:

- Treating it as one product when it is two (a CLI generator and a generated client) and confusing the two audiences.
- Showing examples that no longer match what the generator emits — templates moved, the doc didn't.
- Showing a sync example without the async counterpart (or vice versa); the generator emits both, the docs must show both.
- Duplicating internals from `design/REQUIREMENTS.md` / `design/DESIGN.md` into `docs/`. They are different audiences with different lifecycles. **Never copy prose between them.**

Read every paragraph as a tired user who just wants to ship a client.

## Three audiences, one site

Identify which audience the page is serving before writing. Most pages serve exactly one.

1. **Generator users** — they run `okapipy spec generate`. They care about the CLI, the rules file, `x-okapipy-*` extensions, paginators, and the resulting project shape. Lives in: `docs/user-guide/installation.md`, `quick-start.md`, `rules.md`, `strategies.md`, `templates.md`, and `reference/cli.md`.
2. **Generated-client consumers** — they `import` the emitted package and call methods. They care about filters, sorts, pagination, sync/async, error handling, retries, and `RequestOptions`. Lives in: `docs/user-guide/client-usage.md`. **The CLI is invisible to this reader.**
3. **Customizers and extenders** — they subclass `*Base` classes, override Jinja templates, write custom strategies, or override model templates. Lives in: `docs/user-guide/customization.md`, `templates.md`, parts of `strategies.md`.

A page that drifts across two of these is the single most common structural problem. If you find one, the right move is usually to split, not to keep editing in place.

## Skill range — from junior to senior in the same page

Readers span a wide range. The site must serve a junior who has never used `httpx` *and* a senior who wants the override hook. The discipline:

- **Lead with the concrete first success.** A junior reader should be able to copy-paste their way to a working result without scrolling.
- **Use progressive disclosure.** Anything advanced (custom strategies, template overrides, `RequestOptions`, retry tuning) goes in a clearly-marked later section or its own page, not in the middle of the basics.
- **Define okapipy jargon on first use** — even our own terms. "Collection (a path that fans out into many resources, like `/orders`)" beats a bare "Collection". Link the second mention to the explanation, not redefine.
- **Don't confuse "informal" with "imprecise".** The voice is informal; the technical claims are exact.
- **Never write "simply" or "just".** They make a stuck reader feel stupid.

## Doc map — mirror this exactly

The mkdocs nav (`mkdocs.yml`) is the authoritative structure. Anchor every change to it. As of today:

```
Home                              docs/index.md
User Guide
    Installation                  docs/user-guide/installation.md
    Quick start                   docs/user-guide/quick-start.md
    Using the client              docs/user-guide/client-usage.md
    Rules and extensions          docs/user-guide/rules.md
    Pagination strategies         docs/user-guide/strategies.md
    Code customization            docs/user-guide/customization.md
    Templates                     docs/user-guide/templates.md
Developer Guide                   docs/developer-guide/{index,parser,generator}.md
Reference                         docs/reference/{cli,parser,generator}.md
```

Rules that follow from the structure:

- New CLI flag → update `reference/cli.md` **and** the user-guide page that uses it. CLI reference is auto-generated tone but still hand-curated.
- New `x-okapipy-*` extension → `user-guide/rules.md` is the home; the explanation page (`developer-guide/parser.md`) gets the *why* if non-obvious.
- New paginator/strategy → `user-guide/strategies.md`; if it changes generated-client surface, also `client-usage.md`.
- New template override knob → `user-guide/templates.md`; mention briefly in `customization.md` if it overlaps.
- API surface change in the generated client → `client-usage.md` first, `customization.md` only if the override path changes.
- Reference pages under `reference/` are mkdocstrings-driven. Curate the docstrings; do not rewrite the reference page by hand.

## Diátaxis lens — applied to okapipy

Every page is doing exactly one of four jobs:

- **Tutorial** — `quick-start.md`. Linear, opinionated, no choices, ends with a working client.
- **How-to** — `client-usage.md` (sectioned by task: filter, sort, paginate, run async, handle errors, retry), `customization.md`, `templates.md`. Get to the point. Assume basic familiarity.
- **Reference** — `reference/cli.md`, `reference/parser.md`, `reference/generator.md`, plus the table-style sections of `rules.md` (extension list) and `strategies.md` (built-in strategy table). Predictable, complete, terse.
- **Explanation** — `developer-guide/index.md`, `parser.md`, `generator.md`. Discursive is OK; this is *why* and *how it fits together*, not *how to do X*.

If a page tries to do two jobs at once, split it.

## Three workflows

Pick the workflow before writing.

### Workflow A — Documenting a new feature

A new feature usually requires updates in **at least three places**:

1. **The relevant reference entry** — a flag in `reference/cli.md`, an extension row in `rules.md`, a strategy entry in `strategies.md`, a template variable in `templates.md`. Match the existing template on that page exactly.
2. **At least one how-to or tutorial that actually exercises it.** If the feature changes a workflow users already do (parsing, generating, calling the client, customizing), find the existing page and update it — don't create a new one alongside.
3. **Release notes** (if this lands as a release). One user-facing line. "Add `--strip-prefix` to `spec generate`" beats "feat(generator): plumb strip_prefix through pipeline (#NNN)".

Optional but often needed:

4. **Quick start**, if the feature changes what a brand-new user learns first.
5. **Developer guide**, if the feature introduces a new concept users must reason about (a new node kind, a new lifecycle, a new precedence rule).

Before declaring the page list complete, grep `docs/` and the README for the feature's keywords plus any names it interacts with. Stale references hide everywhere.

### Workflow B — Improving existing docs

Don't rewrite for the sake of rewriting. Diagnose first:

1. **Read the page as a stranger.** What's the page trying to do (Diátaxis category, audience)? Does it succeed? What question does it leave unanswered?
2. **Check the examples against the actual generator output.** Run `okapipy spec generate` on a fixture if needed. Stale snippets are the #1 trust-killer in a generator's docs.
3. **Check sync/async parity.** Most client-facing examples should appear in both forms (mkdocs-material content tabs are the canonical mechanism — see `pymdownx.tabbed`).
4. **Cut before adding.** Most pages improve by removing throat-clearing ("Welcome to X! In this section…") and lead-burying. Lead with the thing.
5. **Then add what's actually missing** — usually a runnable example, an edge case, a one-line summary up top, or a "see also" link.

If a page is doing two Diátaxis jobs at once, splitting it is usually a bigger improvement than any amount of editing in place.

### Workflow C — Drift fix after generator/template/runtime change

okapipy doc drift is sneakier than most projects' because the generator's *output* drives most examples. The protocol:

1. **Identify the surface that changed.** A template? A runtime API in `generator/runtime/`? A CLI flag? An emitted naming convention (`*Base`, `__<child>_factory__`, `<Coll>BaseIterator`)?
2. **Grep the docs and README** for the old name, the old phrase, and any verbatim code snippets that depend on the old shape.
3. **Verify against truth:**
   - For CLI: the actual `--help` output. Reference page and `--help` must agree word-for-word on flag names, defaults, and behavior.
   - For emitted code shape: actually run `okapipy spec generate` on a representative fixture (`tests/fixtures/`) and diff the relevant generated file against what the doc shows.
   - For runtime APIs: the source under `src/okapipy/generator/runtime/` is the contract.
4. **Update reference first**, then how-to/tutorial pages, then quickstart, then the README. The README's example must match the real first-run experience.
5. **Add a deprecation note** for breaking changes — clear before/after, version where it changed.

## Page templates

### Quick start (`docs/user-guide/quick-start.md`)

The most important page on the site. If it fails, nothing else matters.

- **One path, one outcome.** Not "you can do A, B, or C." Pick what most users want (today: parse a spec → generate a client → call one operation) and ship them there.
- **Under five minutes** from landing on the page to seeing real output.
- **Every step ends with verification.** "Run `X`. You should see `Y`." So the reader knows when to stop and fix.
- **No detours.** No design philosophy, no alternative paths. Link to those for the curious.
- **End with explicit next steps.** Two or three links: deeper how-to (`client-usage.md`), customization story (`customization.md`), CLI reference (`reference/cli.md`).

### CLI reference entry (`reference/cli.md`)

Every command and subcommand uses this template. Consistency is what makes the page useful at scan speed.

```markdown
## `okapipy <command>`

One-sentence description. Active voice, present tense. No "this command will…".

### Synopsis

```
okapipy <command> [OPTIONS] <REQUIRED> [OPTIONAL]
```

### Description

Two or three sentences on what it does and when you'd reach for it. If a non-obvious mental model is needed (e.g. parser pipeline, two-layer output), link to the explanation page — don't re-explain here.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--rules <path>` | _none_ | Path to a project-local rules file. Wins over spec values. |
| `--strip-prefix <prefix>` | _none_ | Trim a leading path segment before classification. |

### Examples

#### Basic usage

```bash
$ okapipy spec parse openapi.yaml
namespaces=2 collections=7 resources=7 actions=3 …
```

#### With a rules file

```bash
$ okapipy spec parse openapi.yaml --rules okapipy.rules.yaml --output tree.json
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | `ParserError` / `GenerationError` |
| 2 | Invalid arguments |

### See also

- [Quick start](../user-guide/quick-start.md)
- [Rules and extensions](../user-guide/rules.md)
```

Rules: defaults go in the table, not in prose. Always show output. Never invent flags — verify against `okapipy <command> --help` first.

### How-to (most user-guide pages)

- One page per task family (using filters, customizing the client, overriding templates). Don't fragment into one-page-per-method.
- Lead with the simplest end-to-end example. Show the **command and the output**, or the **code and the result**.
- Sync and async **must** appear together where the generated surface offers both. Use mkdocs-material content tabs:

  ```markdown
  === "Sync"
      ```python
      orders = client.commerce.orders.list(filter=Filter(...))
      ```
  === "Async"
      ```python
      orders = await client.commerce.orders.list(filter=Filter(...))
      ```
  ```
- Use realistic data, not `foo`/`bar`. Use the okapipy domain (orders, customers, line items) so the example reads like real code.
- Cross-link the first mention of every concept (`Collection`, `Action`, `RequestOptions`, `Filter`, `Sort`, `Strategy`) to its definition.

### Explanation (`developer-guide/*`)

Discursive is OK. Goal: a reader walks away understanding *why* the parser is a pipeline, *why* customization is two-layer, *why* the runtime is vendored. State the mental model up front, then ground it with one concrete example. Cite the design docs by section number (`design/DESIGN.md §2.4`) for maintainer-level depth — don't copy their prose.

## Code and command examples — non-negotiable rules

- **Every example runs as written.** If a placeholder is needed (a spec path, a package name), make it visually obvious and explain in the next line where to get it.
- **Show input and output.** Use `$` for the prompt; show the panel/JSON/error the user actually sees.
- **Use realistic, domain-flavored data.** `acme.commerce`, `Order`, `submit`. Not `foo.bar`, `Thing`, `do_it`.
- **Match the emitted code shape exactly.** Class names, factory dunders (`__<child>_factory__`), iterator suffixes (`<Coll>BaseIterator` / `Async<Coll>BaseIterator`), `count()` only when `supports_count=True`. If you change a template, sweep every snippet that referenced the old shape.
- **Generated clients depend on `httpx` and `pydantic` directly — not on okapipy.** Imports in user-facing examples must reflect this. Never write `from okapipy.runtime import …` in a generated-client example; it's `from acme.commerce.base.runtime import …` (or whatever package the user generated into).
- **One-shot vs base.** When showing layout or paths, mark which files are one-shot (user owns) and which are regenerated (`base/` — never edited).
- **Pin against a known generator version** in tutorials if the output would differ across versions.

## Okapipy-specific drift hazards

These bite this project specifically. Re-check on every change:

1. **Names of the five node kinds:** `Namespace`, `Collection`, `Resource`, `Singleton`, `Action`. Plus the parser's classifier label `RESOURCE_ID`. Don't invent synonyms.
2. **Operation routing rules:** `Collection` accepts only `GET → fetch` and `POST → create`. `Resource` and `Singleton` accept the standard CRUD verbs. `Action` collects any verb. Anything else is dropped with a warning, not coerced.
3. **Rules-file values win over spec values** — every conflict, every extension. Say so on the page that introduces the conflict.
4. **`x-okapipy-*` extension list** lives in `rules.md`. Every extension appears with: the spec-side key, the rules-file equivalent, and the precedence note. Adding an extension and not updating both columns is the most common drift bug.
5. **`x-okapipy-exclude`:** `"*"` skips a whole path; a list like `[DELETE, ...]` (case-insensitive) skips specific methods. Both forms must show up in the example for the extension.
6. **Two-layer customization:** the user layer is one-shot; `base/` is rewritten on every run. Never demonstrate editing under `base/`.
7. **Wiring lives on `*Base`.** User stubs specialize via `__<child>_factory__` ClassVars. Customization examples must subclass — never redeclare wiring.
8. **`count()` is conditional** on `PaginationStrategy.supports_count`. Don't show `.count()` on a collection whose strategy doesn't support it.
9. **Models** (`base/models.py`) are emitted by `datamodel-code-generator`. Don't claim okapipy generates them itself; describe the override path through `--model-templates-dir`.
10. **`--check`** is the CI gate — exits non-zero on diff, on stale base files, on drift warnings. Document it in CI how-to contexts, never as a "preview" mode.
11. **CLI source URL or path:** `SOURCE` accepts file paths and `http(s)` URLs. Mention both, or you'll get an issue asking why URLs work.

## Vocabulary — use exactly these words

- The tool is **okapipy** (lowercase, one word).
- The CLI is **`okapipy`**, with subcommands **`spec parse`**, **`spec generate`**, **`nlp fetch`**.
- The output package is the **generated client** (or "the emitted client"). Not "the SDK", not "the library".
- The two layers are the **base layer** (`base/`, regenerated) and the **user layer** (everything outside `base/`, one-shot). Use those names exactly.
- Node kinds: `Namespace`, `Collection`, `Resource`, `Singleton`, `Action`. Capitalized when referring to the node concept.
- The rules file is **the rules file** (a project-local YAML/JSON file). Not "the config", not "the override file".
- Spec-side configuration is via **OpenAPI extensions** (e.g. `x-okapipy-kind`).
- Pagination logic is a **strategy** (`OffsetLimit`, `PageNumber`, `Cursor`, `Custom`). The interface lives in `runtime/strategies.py` in every generated package.

## `design/` vs `docs/` — never duplicate

`design/REQUIREMENTS.md` and `design/DESIGN.md` describe the contract a maintainer must honor. They are checked into the repo, not published, and are reverse-engineered from source. **They are not for users.**

- When something is true of the user experience (CLI flags, generated client surface, customization story), document it in `docs/`.
- When something is an internal contract a maintainer must honor (parser pipeline phases, builder invariants, in-place mutation rules), keep it in `design/`.
- If you find prose in one that belongs in the other, move it. Don't copy.
- It is fine for `docs/developer-guide/*.md` to summarize a *user-visible* shape and link to a design section by number for maintainer depth. The reverse — pasting design prose into user docs — is forbidden.

## mkdocs-material features to use (and not abuse)

The site is configured for these. Reach for them when they help the reader; don't sprinkle for decoration.

- **Content tabs** (`pymdownx.tabbed`) — sync vs async, JSON vs YAML, file vs URL. Anything where the reader picks one path.
- **Admonitions** (`!!! note`, `!!! warning`, `!!! tip`) — for non-obvious gotchas (rules-file precedence, `--check` exit codes, the "never edit `base/`" rule). Keep them short. One per page is usually plenty.
- **Tables** for reference data — flag tables, extension tables, exit codes. Never reach for tables to lay out prose.
- **Code-block annotations** (`# (1)!` syntax) — useful in long examples where two or three lines deserve a spotlight. Use sparingly; they hide content from text search.
- **Mermaid** — only when a diagram replaces prose, not in addition to it. The parser pipeline is a candidate; CRUD verb routing usually isn't.

Do **not** add navigation features, plugins, or markdown extensions without a documented reason and a corresponding `mkdocs.yml` change.

## Voice and style

- **Imperative for instructions.** "Run the command", not "you should run the command".
- **Second person for the reader.** "You" exists. Avoid "we" — it's vague.
- **Active voice, present tense.** "The generator writes `base/` on every run", not "Base is written by the generator on every run".
- **One idea per sentence.** A sentence with more than one comma probably wants to be two.
- **Define jargon on first use.** Even okapipy's own jargon. Link the second mention.
- **Cut filler.** "It is important to note that", "in order to", "as you can see", "simply", "just" — all deletable. Especially "simply" and "just".
- **Informal, not imprecise.** Contractions are fine. Loose technical claims are not.

## Findability

Most readers arrive via search.

- Page titles match what users search for. "Code customization", not "User-layer subclassing model".
- Headings are scannable. A reader skimming should grasp the page structure without reading prose.
- **Error messages and warning strings appear verbatim** somewhere in the docs with their cause and the fix. When a user pastes `dropped operation: PUT on Collection 'orders'` into Google, the docs should be the first hit.
- Cross-link aggressively. Internal links are free.
- Don't bury the lede. The first paragraph answers "what is this page about and is it the page I want?"

## Be honest

- **Document limits and gotchas.** "Currently does not support `oneOf` schemas in request bodies" is more trustworthy than silence.
- **Document failure modes.** What does a `ParserError` look like on the CLI? What's a typical `GenerationError`? Put a "Troubleshooting" section on pages where users hit walls.
- **Don't oversell.** Marketing copy in technical docs erodes trust.
- **Mark experimental surfaces.** A clear "Experimental — API may change" callout beats discovering it the hard way.

## Self-check before declaring a doc change done

If any answer is "no" or "I don't know", fix it.

1. **Diátaxis category clear?** Is this page doing exactly one job?
2. **Audience clear?** Generator user, generated-client consumer, or customizer/extender? One per page, ideally.
3. **Examples actually run?** Did you run `okapipy spec parse` / `spec generate`, or call the generated client, against a real fixture?
4. **Output shown?** Every example ends with what the user should see.
5. **Sync/async parity?** Where the generated surface offers both, both appear.
6. **Emitted code shape verified?** Class names, factory dunders, iterator suffixes, `count()` conditionality — all match the current templates.
7. **Cross-references current?** Did you update other pages that reference the changed thing?
8. **`--help` and reference page agree?** Word-for-word on flag names and defaults.
9. **Quickstart still under five minutes?** If you touched it, time it.
10. **No drift into `design/`?** Nothing pasted from REQUIREMENTS or DESIGN; references are by section number.
11. **Cuts made?** Did you remove at least as many words as you added, where possible?

## Anti-patterns to refuse

Push back before doing any of these:

- **"Just add a page about X"** without checking whether X belongs on an existing page or whether other pages need updating too. Fragmentation is a slow-motion disaster on a site this size.
- **"Make the quickstart cover everything."** Quickstarts that try to cover everything teach nothing. Offer to split into a quickstart plus targeted how-tos.
- **"Document the parser/generator internals on the user-facing site."** Internals belong in `design/` or the developer guide's *user-relevant* concepts. The internal pipeline phases are not user-facing.
- **"Auto-generate the CLI reference from `--help`."** A starting point is fine; ship-quality reference needs hand-curated examples, exit codes, and "see also" links.
- **"Show only the async example, sync is obvious."** It is not. The generator emits both; the docs show both.
- **"Hide the limitations to make it look better."** Refuse. Costs trust now and costs more in support later.
- **"Copy the relevant section from `design/DESIGN.md` into the developer guide."** Refuse. Link to it; never copy.

## When in doubt

- Ask: "What is the user trying to do when they land on this page?" If you can't answer in one sentence, the page needs work before any prose gets written.
- Ask: "Which of the three audiences is this for?" If the answer is two, split.
- Bias toward fewer, better pages over many thin ones.
- Bias toward concrete, runnable examples over abstract description.
- Bias toward the user's words over the codebase's words — but never at the cost of technical precision.
