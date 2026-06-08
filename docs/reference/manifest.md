# Manifest reference

The project manifest is the single YAML (or JSON) file that drives
`okapipy generate`. Scaffold one with [`okapipy init`](cli.md#okapipy-init);
edit it; check it into your repo. The CLI looks for `./okapipy.yml` by
default.

This page lists every field the manifest accepts — what each one does,
what it defaults to, and what it shows up as in the generated project.
For the *commands* that read it, see the [CLI reference](cli.md). For a
guided first run, see the [quick start](../user-guide/quick-start.md).

## Shape

```yaml
# okapipy.yml
package: acme.commerce              # required
client_class: CommerceClient        # required

# Project metadata
project_name: acme-commerce
project_description: Acme commerce API client
project_version: "0.1.0"
python_version: "3.13"
license: Apache-2.0
author: Acme Corp
repo_url: https://github.com/acme/commerce

# Generation settings
shape: auto                         # auto | models | dicts
lang: en
nlp_cache_dir: .spacy
templates_dir: ./templates
model_templates_dir: ./model_tpls

# Output
output: ./out

specs:                              # required, at least one
  - namespace: ''
    source: ./openapi.yaml
    rules: ./rules.yaml
    strip_prefix: /v1
    unmatched: misc
    lang: en
```

Unknown top-level fields and unknown per-spec fields are **rejected** —
typos surface as a validation error rather than a silent no-op. Paths
are resolved relative to the manifest file's parent directory, so the
manifest is portable with the consumer repo. URL `source` values
(`http://…` / `https://…`) are passed through verbatim.

## Required fields

| Field | Type | Description |
| --- | --- | --- |
| `package` | string | Dotted Python package path for the generated client (e.g. `acme.commerce`). Drives the `src/<package>/` layout and the import path consumers will use. |
| `client_class` | string | PascalCase class name for the sync client. The async sibling is named `Async<client_class>` automatically. Base classes carry an additional `Base` suffix. |
| `specs` | list of [spec entries](#per-spec-entries) | At least one entry; each one mounts an OpenAPI document under a namespace in the generated client. |

## Project metadata

These fields drive the generated `pyproject.toml`, `LICENSE`, and
`README.md`. They are PEP 621-flavored — see the [packaging user
guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
for the upstream contract.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `project_name` | string | last segment of `package` | PEP 503 distribution name. Becomes `[project] name`. |
| `project_description` | string | `"Generated client for <project_name>"` | Free-form short summary. Becomes `[project] description`. |
| `project_version` | string | `"0.1.0"` | Initial version string. Becomes `[project] version`. |
| `python_version` | string | `"3.13"` | Pinned Python minor version. Becomes `[project] requires-python = ">=<version>,<4"` and the project's `.python-version`. |
| `license` | string | `"Proprietary"` | SPDX license id. Recognised values (`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `MPL-2.0`) emit the full license text into `LICENSE` **and** an SPDX `[project] license = "..."` line. Free-form values like `Proprietary` emit a placeholder `LICENSE` and omit the SPDX line — see [License handling](#license-handling). |
| `author` | string | _none_ | Copyright holder for `LICENSE` and the `[project] authors = [{ name = "..." }]` entry. When omitted, the LICENSE falls back to the project name and `pyproject.toml` omits the authors block. |
| `repo_url` | string | _none_ | Source-repository URL. When set, drives `[project.urls]` with `Homepage` and `Repository` pointing at the URL. `github.com` URLs also gain a synthetic `Issues = "<url>/issues"` entry. |

### License handling

okapipy recognises a small set of common SPDX identifiers. The behavior
depends on whether yours is on the list.

| `license:` value | `LICENSE` file | `[project] license =` line |
| --- | --- | --- |
| `MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `MPL-2.0` | Full canonical license text | Emitted verbatim |
| Anything else (default: `Proprietary`) | Placeholder with a `TODO: replace with the full license text` line | Omitted |

`license-files = ["LICEN[CS]E*"]` is **always** emitted because okapipy
always writes a `LICENSE` file alongside the generated project.

If you need an SPDX expression that isn't on the safelist (a custom
`LicenseRef-Proprietary`, a compound expression like `MIT OR
Apache-2.0`), generate first, then edit `pyproject.toml` by hand —
it's one-shot, so your edits survive subsequent runs.

### URL handling

`repo_url` is intentionally narrow. One URL in, up to three labels out:

```toml
[project.urls]
Homepage = "https://github.com/acme/commerce"
Repository = "https://github.com/acme/commerce"
Issues = "https://github.com/acme/commerce/issues"   # github.com only
```

For richer `[project.urls]` (Documentation, Changelog, Funding, …),
edit `pyproject.toml` after the first run. It's one-shot.

## Generation settings

These shape *how* the generator turns parsed trees into code. They are
project-wide; per-spec overrides are documented under [per-spec
entries](#per-spec-entries).

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `shape` | `auto` \| `models` \| `dicts` | `auto` | Response-shape policy. `auto` emits a dual-shape client whose bodies and returns admit `Foo \| dict[str, Any]`. `models` locks every body and return to typed Pydantic. `dicts` locks them to `dict[str, Any]` and skips `base/models.py` entirely. See [Response shape](../user-guide/shapes.md). |
| `lang` | string | `"en"` | Default ISO language code for the NLP pipeline. Per-spec `lang` overrides this. |
| `nlp_cache_dir` | path | `<cwd>/.spacy` | Directory in which spaCy models are stored. Resolved relative to the manifest file. |
| `templates_dir` | path | _none_ | Directory of user-authored Jinja templates. Resolved before the packaged defaults via a `ChoiceLoader`. See [Templates](../user-guide/templates.md). |
| `model_templates_dir` | path | _none_ | Directory of `datamodel-code-generator` templates. Forwarded as dmcg's `custom_template_dir`. |

## Output

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `output` | path | _none_ | Directory to write the generated project into. Resolved relative to the manifest file. The CLI's `--output` flag overrides this; one of the two is required. |

## Per-spec entries

`specs:` is a list of entries. Each entry pairs one OpenAPI document
with the namespace it mounts under. A single-entry manifest with
`namespace: ''` mounts the spec at the root — the historical
single-spec layout. Multiple entries compose under one client, with
each spec keeping its own per-spec inputs.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `namespace` | string | required | Dotted mount path. `''` mounts at the package root. Dotted values (`platform.users`) nest under intermediate namespaces synthesized as `Namespace` nodes and may be shared by multiple specs (`platform.billing` joins the same `platform/` parent). Two entries resolving to the same fully-qualified mount path are rejected at load time. |
| `source` | string | required | Path or `http(s)://` URL of the OpenAPI document. Paths resolve relative to the manifest file. |
| `rules` | path | _none_ | Local path to a JSON/YAML rules file. **URLs are rejected.** Mirrors the OpenAPI extension shape and overrides any spec value on conflict — see [Rules and extensions](../user-guide/rules.md). |
| `strip_prefix` | string | _none_ | Path prefix to strip from every operation in this spec before classification. Overrides the prefix inferred from `servers[].url`. |
| `unmatched` | string | _none_ | Top-level namespace name (inside this spec's mount) to hold operations the routing table would otherwise drop. Equivalent to the parser's `--unmatched` flag. |
| `lang` | string | inherits top-level `lang` | ISO language code for the NLP pipeline for this spec. |

### Multi-spec composition

Two examples that compose three services into one client:

=== "Flat mounts"

    ```yaml
    package: acme.platform
    client_class: PlatformClient
    output: ./out

    specs:
      - namespace: auth
        source: ./specs/auth.yaml
      - namespace: billing
        source: ./specs/billing.yaml
      - namespace: orders
        source: ./specs/orders.yaml
    ```

=== "Shared parent namespace"

    ```yaml
    package: acme.platform
    client_class: PlatformClient
    output: ./out

    specs:
      - namespace: platform.auth
        source: ./specs/auth.yaml
      - namespace: platform.billing
        source: ./specs/billing.yaml
      - namespace: platform.orders
        source: ./specs/orders.yaml
    ```

Consumers reach each service through a top-level accessor on the
client (`client.auth.…`, `client.platform.auth.…`). For the design
behind multi-spec composition see [Advanced
usage](../user-guide/advanced.md).

## What does **not** live in the manifest

A few things look like they belong here but don't — they are deliberate
gaps, not oversights:

* **Dependencies and dev-dependencies.** The generated `pyproject.toml`
  pins `httpx` and `pydantic[email]` at versions matching the vendored
  runtime, and the dev group ships `pytest`, `ruff`, `mypy`, and
  friends. These are not user-configurable; the runtime requires them.
  Add your own dependencies by editing `pyproject.toml` after the first
  run — it's one-shot, your edits survive.
* **Keywords, classifiers, maintainers, additional `project.urls`
  labels.** Not exposed by the manifest. Edit `pyproject.toml` by hand;
  one-shot lifecycle.
* **README content, CHANGELOG.** The generator writes a starter
  `README.md` once; the file is one-shot, so further edits are yours.
* **Pre-commit hooks, CI workflows, formatter configs beyond what's
  emitted.** Out of scope; add them yourself.
* **CLI flags.** Only `--output` overrides a manifest field
  (`output`). `--check` and `--quiet` are run-time switches with no
  manifest counterpart. Everything else lives only in the manifest.

If you reach for a field that isn't here, the answer is almost always
"edit `pyproject.toml` after the first run." That file is one-shot —
your edits survive subsequent regenerations.

## Errors

`okapipy generate` exits with `1` and a stderr panel when the manifest
fails to load. Common cases:

| Error | Cause |
| --- | --- |
| `ManifestNotFoundError` | The file at `--manifest PATH` (default `./okapipy.yml`) does not exist. |
| `ManifestFormatError: ... is not valid JSON/YAML` | The file exists but does not parse. |
| `ManifestFormatError: ... failed validation: ...` | A required field is missing, a value has the wrong type, or an unknown field is present. The trailing detail names the offending field. |
| `ManifestFormatError: mount namespace collision` | Two `specs[]` entries resolve to the same fully-qualified namespace. |
| `ManifestFormatError: rules must be a local path, got URL` | A `specs[].rules` value uses an `http(s)://` scheme. Rules files are local-only. |

## See also

* [Quick start](../user-guide/quick-start.md) — your first `okapipy.yml`.
* [`okapipy init`](cli.md#okapipy-init) — scaffold a starter manifest.
* [`okapipy generate`](cli.md#okapipy-generate) — what the manifest drives.
* [Rules and extensions](../user-guide/rules.md) — what goes in `rules:` files.
* [Response shape](../user-guide/shapes.md) — what the `shape:` field controls.
* [Templates](../user-guide/templates.md) — what the `templates_dir:` field overrides.
