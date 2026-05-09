# CLI reference

The `okapipy` command is a [Typer][typer] application with two
sub-apps: `nlp` and `spec`. Run `okapipy --help` (or any subcommand
with `--help`) for the canonical, version-pinned usage; this page is a
human-readable summary.

```text
okapipy [-v | -vv] {nlp | spec} ...
```

The top-level `-v` / `-vv` flag controls log verbosity (INFO / DEBUG).
Without it, only WARNING and above reach stderr.

---

## `okapipy nlp`

Manage local spaCy NLP models.

### `okapipy nlp fetch`

Download and install the spaCy model for a language into a local cache.

```bash
okapipy nlp fetch <LANG> [--cache-dir DIR]
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

## `okapipy spec`

Inspect and parse OpenAPI specifications, and generate clients from
them.

### `okapipy spec parse`

Parse an OpenAPI document and either print the resulting structural
tree as JSON to stdout, or save it to a file.

```bash
okapipy spec parse <SOURCE> [OPTIONS]
```

| Argument / option | Default | Description |
| --- | --- | --- |
| `SOURCE` | required | Path or http(s) URL of the OpenAPI document. |
| `--rules PATH` | none | Local path to a JSON/YAML rules file. |
| `--lang CODE` | `en` | ISO language code for NLP. |
| `--strip-prefix STRING` | `None` | Path prefix to strip from every path before classification (e.g. `/public/v1`). Overrides the prefix inferred from `servers[].url`. |
| `--nlp-cache-dir DIR` | `./.spacy` | Where to look for / store the spaCy model. |
| `--output PATH` | none | Write the parsed tree to a file. Format inferred from `.json` / `.yaml` / `.yml` extension. |

Output:

* **stderr** — a counts panel (namespaces / collections / resources /
  actions) and any non-fatal warnings.
* **stdout** — JSON dump of the parsed tree, syntax-highlighted on a
  TTY, plain when piped.

Exit codes:

* `0` — parse succeeded.
* `1` — parser raised; the error message is printed to stderr.

### `okapipy spec generate`

Generate a Python client project from an OpenAPI document.

```bash
okapipy spec generate <SOURCE> --output DIR --package PKG --client-class NAME [OPTIONS]
```

**Required**:

| Option | Description |
| --- | --- |
| `--output DIR`, `-o DIR` | Directory to write the generated project into. |
| `--package PKG` | Dotted Python package path (e.g. `acme.commerce`). |
| `--client-class NAME` | PascalCase class name for the sync client. The async sibling is `Async<name>`. |

**Project metadata**:

| Option | Default | Description |
| --- | --- | --- |
| `--project-name NAME` | last segment of `--package` | PEP 503 distribution name. |
| `--project-version V` | `0.1.0` | Initial version string emitted into `pyproject.toml`. |
| `--python-version V` | `3.13` | Pinned Python version for the generated project. |
| `--license SPDX` | `Proprietary` | SPDX license identifier; drives the `LICENSE` placeholder. |

**Parser options** (forwarded to `okapipy spec parse`):

| Option | Default | Description |
| --- | --- | --- |
| `--rules PATH` | none | Local path to a JSON/YAML rules file. |
| `--lang CODE` | `en` | ISO language code for NLP. |
| `--strip-prefix STRING` | `None` | Path prefix to strip before classification. |
| `--nlp-cache-dir DIR` | `./.spacy` | spaCy model cache. |

**Generator options**:

| Option | Default | Description |
| --- | --- | --- |
| `--templates-dir DIR` | none | User Jinja templates that override the packaged defaults. See [Template customization](../user-guide/templates.md). |
| `--model-templates-dir DIR` | none | datamodel-code-generator templates for `models.py`. |
| `--shape {models\|dicts}` | unset (dual shape) | Lock the generated client to a single response shape. Omit to produce a dual-shape client (`shape=` constructor + `with_shape()`). `--shape dicts` also skips `base/models.py`. See [Response shape](../user-guide/shapes.md). |
| `--check` | off | Dry-run; report drift, exit non-zero on any change. CI gate. |
| `--quiet`, `-q` | off | Suppress drift-detection warnings. Pruning still runs. |

Exit codes:

* `0` — generated successfully (or `--check` passed with no drift).
* `1` — parse error, generator error, or `--check` found drift.

[typer]: https://typer.tiangolo.com/
