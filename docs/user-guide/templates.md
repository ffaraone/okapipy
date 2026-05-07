# Template customization

Every Python file the generator writes is rendered from a **Jinja2
template**. The templates that ship with okapipy work out of the box for
most projects, but when you need to bend the output to a corporate
style, an internal stack, or an opinionated team convention, you can
override any template per project — without forking the package.

This is a power-user feature. You don't need it to use okapipy, and
you can ignore this page entirely. Reach for it when:

* You want a different file header on every regenerated file (license,
  ownership, "machine-translated, do not edit" wording).
* Your team has a house style for `pyproject.toml`, `LICENSE`,
  `README.md`, or the `.gitignore` of the generated project.
* You want to change the docstring style, add type-hint variations, or
  tweak how operations dispatch.
* You need a different shape for `base/models.py` — the
  `datamodel-code-generator` step has its own template directory.

## How it works

The generator uses Jinja2 with a `ChoiceLoader`:

1. **Your `--templates-dir`** (when supplied) is searched first via
   `FileSystemLoader`. If a template by the same name exists there, it
   wins.
2. **The packaged defaults** in `okapipy.generator.templates` are
   searched second. Anything you didn't override falls through.

Missing templates and missing context variables fail loudly
(`StrictUndefined`); the run aborts with a clear error rather than
silently emitting `""`.

```bash
okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient \
    --templates-dir ./project-templates
```

After rendering, every Python file passes through `ruff check --fix
--select I` (isort) and then `ruff format`. **You don't need to format
the output of your templates** — broken indentation in your jinja is
canonical-formatted on the way out. What you do need to do is emit
*valid* Python; if ruff can't parse it, the run aborts with a
`FormatError` pointing at the template name.

## Available templates

`--templates-dir` looks for templates under `package/`,
`project/`, and `tests/` subdirectories — the same layout as the
packaged defaults. Anything outside those subdirectories is ignored.

### Per-node code (`package/`)

Rendered once per parser-tree node, into `base/<kind>/<name>.py`.

| Template | Output | Variables (selection) |
| --- | --- | --- |
| `package/client.py.jinja` | `base/client.py` | `class_name`, `client_class`, `project_name`, `namespaces`, `collections`, `singletons`, `actions` |
| `package/namespace.py.jinja` | `base/namespaces/<name>.py` | `class_name`, `client_class`, `namespaces`, `collections`, `singletons`, `actions` |
| `package/collection.py.jinja` | `base/collections/<name>.py` | `class_name`, `client_class`, `path_template`, `resource`, `actions`, `create_op`, `fetch_item_model`, `model_imports` |
| `package/resource.py.jinja` | `base/resources/<name>.py` | `class_name`, `client_class`, `path_template`, `id_param`, `retrieve_op`, `update_op`, `partial_update_op`, `delete_op`, `actions`, `collections`, `singletons`, `model_imports` |
| `package/singleton.py.jinja` | `base/singletons/<name>.py` | Same shape as `resource.py.jinja` (singletons share the resource contract). |
| `package/action.py.jinja` | `base/actions/<name>.py` | `class_name`, `client_class`, `path_template`, `method`, `request_model`, `response_model`, `model_imports` |
| `package/models.py.jinja` | `base/models.py` | Wrapping template around the dmcg-emitted source; rarely customized. |

The exact set of context variables for each template is defined by the
emitter that drives it. The fastest way to learn it is to read the
packaged template:

```bash
python -c "import okapipy.generator.templates as t; \
           print(__import__('importlib.resources').resources.files(t).joinpath('package/collection.py.jinja').read_text())"
```

…and copy the parts you care about into your override.

### Project skeleton (`project/`)

Rendered once at first generation, then **never overwritten** —
they're one-shot, like the user-layer subclass stubs. To regenerate
them on demand, delete the file and re-run.

| Template | Output | Variables |
| --- | --- | --- |
| `project/pyproject.toml.jinja` | `pyproject.toml` | `project_name`, `project_version`, `python_version`, `package`, `top_package`, `client_class`, `license` |
| `project/README.md.jinja` | `README.md` | Same as above. |
| `project/LICENSE.jinja` | `LICENSE` | `license` (SPDX), `project_name`, current year. |
| `project/gitignore.jinja` | `.gitignore` | (no variables) |
| `project/python-version.jinja` | `.python-version` | `python_version` |

### Test scaffolding (`tests/`)

Rendered into the generated `tests/` tree at first generation; one-shot
like the project skeleton.

| Template | Output |
| --- | --- |
| `tests/conftest.py.jinja` | `tests/conftest.py` |
| `tests/test_client.py.jinja` | `tests/test_client.py` |
| `tests/resources/<name>.py.jinja` | `tests/resources/test_<name>.py` |
| `tests/collections/<name>.py.jinja` | `tests/collections/test_<name>.py` |
| `tests/actions/<name>.py.jinja` | `tests/actions/test_<name>.py` |
| `tests/namespaces/<name>.py.jinja` | `tests/namespaces/test_<name>.py` |

## Filters available in templates

Beyond Jinja2's built-ins, the generator's environment registers:

| Filter | What it does |
| --- | --- |
| `snake_case` | `"OrderLine"` → `"order_line"`, `"orderLine"` → `"order_line"`. |
| `pascal_case` | `"order_line"` → `"OrderLine"`. |
| `kebab_case` | `"order_line"` → `"order-line"`. |
| `tojson` | JSON literal — for emitting JSON-like payloads inline. |
| `py_repr` | `repr(value)` — Python-literal escape hatch. |
| `py_class_or_none` | Class name (`"Order"`) or the literal `None`; used in operation methods that may have no response model. |

## Worked example: a custom file header

Suppose every regenerated file in your company should carry a license
banner.

1. Create `project-templates/package/`.
2. Copy the packaged `client.py.jinja` over and replace the header:

    ```jinja
    # Copyright {{ "now"|date("Y") }} Acme Corp.  All rights reserved.
    # SPDX-License-Identifier: Proprietary
    #
    # AUTO-GENERATED by okapipy. DO NOT EDIT.
    """{{ client_class }} base class for {{ project_name }}."""

    {# …rest of the template body unchanged… #}
    ```

3. Repeat for any other template whose header you care about
   (`namespace.py.jinja`, `collection.py.jinja`, `resource.py.jinja`,
   `action.py.jinja`).
4. Re-generate:

    ```bash
    okapipy spec generate openapi.yaml \
        --output ./my-client \
        --package acme.commerce \
        --client-class CommerceClient \
        --templates-dir ./project-templates
    ```

Templates you didn't override fall through to the packaged defaults.

## `base/models.py`: a separate template directory

`base/models.py` is produced by
[`datamodel-code-generator`](https://github.com/koxudaxi/datamodel-code-generator)
(dmcg), which has its own templating system separate from okapipy's
Jinja templates. To customize the **model** layout — extra base
classes, custom validators, alternate field aliases —pass
`--model-templates-dir`:

```bash
okapipy spec generate openapi.yaml \
    --output ./my-client \
    --package acme.commerce \
    --client-class CommerceClient \
    --model-templates-dir ./model-templates
```

The directory uses dmcg's own template names, not okapipy's:

| Template | Purpose |
| --- | --- |
| `BaseModel.jinja2` | The body of every emitted Pydantic model. |
| `ConfigDict.jinja2` | The `model_config = ConfigDict(...)` line. |
| `RootModel.jinja2` | Root models (Pydantic v2's `RootModel[T]`). |

A starter set lives at
[sample_model_templates/](https://github.com/ffaraone/okapipy/tree/main/sample_model_templates)
in the okapipy repo — a useful base to fork from. Custom dmcg templates
are documented at
[dmcg's docs](https://docs.koxudaxi.com/datamodel-code-generator/template/).

A common reason to override: switch every field that's nullable in the
spec from `Optional[T] = None` to `T | None = Field(default=None,
validation_alias=AliasChoices(...))` so unknown JSON keys don't crash
deserialization. The default templates do something close to this
already — read them first to see whether you really need an override.

## Tips

* **Treat overrides as code.** Commit `project-templates/` and
  `model-templates/` to your repo; their drift between team members is
  a real source of "works on my machine" confusion otherwise.
* **Override one template at a time.** The packaged templates evolve
  release to release; the more you override, the more you'll need to
  rebase on each upgrade. Override only what you actually care about.
* **Run `--check` after every change.** Template churn shows up as base
  file diffs; `okapipy spec generate --check` is the fastest way to see
  whether your overrides produced expected output.
* **`StrictUndefined` is your friend.** When you reference a variable
  that doesn't exist in the template's context, the run aborts with a
  clear error. Don't disable it.
* **Don't override templates to add new files.** `--templates-dir`
  changes the *content* of files okapipy already emits; it doesn't add
  files okapipy doesn't know about. For genuinely new files, write
  them yourself in the user layer (which the generator never touches).

## When templates are not enough

If you find yourself reaching for things templates can't do — a custom
manifest layout, a different drift-detection algorithm, a separate
output target — open an issue. Most "I want to do X with templates"
problems are actually missing extension points elsewhere; the
`x-okapipy-*` extensions and the rules file already cover most
classification overrides, and the strategies cover most wire-format
overrides. Templates are the right tool for *cosmetic* and
*organizational* tweaks; they shouldn't carry behavior.
