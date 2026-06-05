# CLI reference

The `okapipy` command is a flat [Typer][typer] application with four
top-level commands. Run `okapipy --help` (or any subcommand with
`--help`) for the canonical, version-pinned usage; this page is a
human-readable summary.

```text
okapipy [-v | -vv] {init | generate | parse | fetch-language} ...
```

The top-level `-v` / `-vv` flag controls log verbosity (INFO / DEBUG).
Without it, only WARNING and above reach stderr.

---

## `okapipy fetch-language`

Download and install the spaCy model for a language into a local cache.

```bash
okapipy fetch-language <LANG> [--cache-dir DIR]
```

| Argument / option | Default | Description |
| --- | --- | --- |
| `LANG` | required | ISO language code (e.g. `en`, `es`, `fr`). |
| `--cache-dir` | `./.spacy` | Directory in which to store the downloaded model. |

The cache directory persists across runs; subsequent invocations are
no-ops if the model is already present at the right version. Useful in
CI before any other okapipy command, so the first parse doesn't pay
the network cost.

::: okapipy.parser.nlp.fetch_model

---

## `okapipy parse`

Parse an OpenAPI document and either print the resulting structural
tree as JSON to stdout, or save it to a file.

```bash
okapipy parse <SOURCE> [OPTIONS]
```

| Argument / option | Default | Description |
| --- | --- | --- |
| `SOURCE` | required | Path or http(s) URL of the OpenAPI document. |
| `--rules PATH` | none | Local path to a JSON/YAML rules file. |
| `--lang CODE` | `en` | ISO language code for NLP. |
| `--strip-prefix STRING` | `None` | Path prefix to strip from every path before classification (e.g. `/public/v1`). Overrides the prefix inferred from `servers[].url`. |
| `--nlp-cache-dir DIR` | `./.spacy` | Where to look for / store the spaCy model. |
| `--unmatched NAME` | none | Bulk escape hatch: keep operations that would otherwise be dropped by the routing table as flat actions under a top-level namespace called `NAME`. Each action is named after its `operationId`, falling back to `<method>_<path>` when no `operationId` is declared. `NAME` must not collide with any existing top-level node. See [Rules and extensions](../user-guide/rules.md#i-have-many-non-conforming-endpoints-and-i-dont-want-to-annotate-each-one). |
| `--output PATH` | none | Write the parsed tree to a file. Format inferred from `.json` / `.yaml` / `.yml` extension. |

Output:

* **stderr** — a counts panel (namespaces / collections / resources /
  actions) and any non-fatal warnings.
* **stdout** — JSON dump of the parsed tree, syntax-highlighted on a
  TTY, plain when piped.

Exit codes:

* `0` — parse succeeded.
* `1` — parser raised; the error message is printed to stderr.

---

## `okapipy generate`

Generate a Python client project from the project manifest.

```bash
okapipy generate [--manifest PATH] [--output DIR] [--check] [--quiet]
```

Every project-level setting (package name, client class, response
shape, templates, license) and every per-spec setting (source, rules,
strip-prefix, unmatched namespace, language) lives in the manifest —
typically `./okapipy.yml`, alongside the consumer code. The CLI carries
only the four flags that change between runs, not between projects.
Use [`okapipy init`](#okapipy-init) to scaffold a starter
manifest.

| Flag | Default | Description |
| --- | --- | --- |
| `--manifest PATH` | `./okapipy.yml` | Path to the project manifest (YAML or JSON). |
| `--output DIR`, `-o DIR` | from manifest | Directory to write the generated project into. Overrides the manifest's `output` field; required when the manifest omits `output`. |
| `--check` | off | Dry-run: report drift, exit non-zero on any change. CI gate. |
| `--quiet`, `-q` | off | Suppress drift-detection warnings. Pruning still runs. |

#### Examples

##### Generate from `./okapipy.yml`

```bash
$ okapipy generate
╭──────────────────────────────────────────────────────────────────────╮
│ Wrote 53 files to ./out                                              │
╰──────────────────────────────────────────────────────────────────────╯
```

##### Override the output directory

```bash
$ okapipy generate --output ./build/sdk
```

##### CI gate

```bash
$ okapipy generate --check
╭──────────────────────────────────────────────────────────────────────╮
│ No changes; no drift.                                                │
╰──────────────────────────────────────────────────────────────────────╯
```

#### Output

* **stderr** — a green summary panel naming the output directory and
  the file count (`Wrote 53 files to ./out`). When the parser emitted
  any warnings during the run, the panel appends `; N warning(s)
  emitted` so the count is visible even if individual warnings
  scrolled past in the terminal.

#### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Generated successfully (or `--check` passed with no drift). |
| `1` | `ManifestNotFoundError`, `ManifestFormatError`, `ParserError`, `GenerationError`, or `--check` found drift. |

#### See also

* [The project manifest](../user-guide/quick-start.md#the-project-manifest) — schema and conventions.
* [Rules and extensions](../user-guide/rules.md)
* [Response shape](../user-guide/shapes.md)

---

## `okapipy init`

Scaffold a starter `okapipy.yml`.

```bash
okapipy init [<SOURCE>] [--manifest PATH] [--package DOTTED] [--client-class NAME] [--force]
```

Writes a starter manifest you can then edit and run `okapipy
generate` against. Without `SOURCE`, the starter has an empty
`specs:` array and inline comments demonstrating the multi-spec
shape — fill in the entries by hand. With `SOURCE`, the starter
contains one root-mount spec entry pointing at it. Either way, when
`--package` or `--client-class` are omitted, the starter carries
`TODO` placeholders so it fails validation on the first `generate`
until you edit them — protection against accidentally generating
against the wrong package name.

| Flag | Default | Description |
| --- | --- | --- |
| `SOURCE` | none | Path or `http(s)://` URL of an OpenAPI document. When given, becomes the single `specs[]` entry's `source`. |
| `--manifest PATH` | `./okapipy.yml` | Where to write the starter manifest. |
| `--package DOTTED` | _TODO placeholder_ | Dotted Python package path. |
| `--client-class NAME` | _TODO placeholder_ | PascalCase client class name. |
| `--force`, `-f` | off | Overwrite an existing manifest. |

#### Examples

##### Scaffold from scratch

```bash
$ okapipy init
╭──────────────────────────────────────────────────────────────────────╮
│ Wrote starter manifest to okapipy.yml                                │
╰──────────────────────────────────────────────────────────────────────╯
```

##### Scaffold for a known spec

```bash
$ okapipy init ./openapi.yaml \
    --package acme.commerce --client-class CommerceClient
```

#### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Wrote the starter manifest. |
| `1` | Refused to overwrite an existing manifest without `--force`. |

[typer]: https://typer.tiangolo.com/
