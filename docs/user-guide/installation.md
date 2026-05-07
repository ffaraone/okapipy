# Installation

okapipy needs **Python 3.12 or newer**. Anything older won't work — the
codebase uses 3.12-only syntax features (PEP 695 type aliases, `match`
exhaustiveness checks via mypy strict, and Pydantic v2's `BaseModel`
ergonomics).

## With pip

The most common path:

```bash
pip install okapipy
```

That installs the CLI (`okapipy`) and the importable package
(`import okapipy`).

If you only need the CLI for a one-off generation, [pipx][pipx] keeps it
isolated from your project:

```bash
pipx install okapipy
```

## With uv

If you're already on [uv][uv] (the project itself uses it), add okapipy to
your project:

```bash
uv add okapipy
```

…or use it without installing anything globally:

```bash
uvx okapipy --help
```

## Verify the install

```bash
okapipy --help
```

You should see two sub-apps: `nlp` and `spec`.

## Pre-warm the spaCy model

The first time the parser runs, it downloads the spaCy English model
(`en_core_web_sm`, ~12 MB) into `./.spacy/`. That's a one-time cost, and
you can move it earlier:

```bash
okapipy nlp fetch en
```

In CI, cache `./.spacy/` between runs so you don't pay the download on
every job:

```yaml
# .github/workflows/your-workflow.yml
- uses: actions/cache@v4
  with:
    path: .spacy
    key: spacy-en_core_web_sm-${{ runner.os }}

- run: okapipy nlp fetch en
```

If you'd rather store the model somewhere else, every command that uses
NLP accepts `--nlp-cache-dir`:

```bash
okapipy nlp fetch en --cache-dir ~/.cache/okapipy/nlp
```

## Other languages

okapipy uses spaCy, which ships pipelines for 20+ languages. Use the
ISO code on `nlp fetch` and again on `spec parse`/`spec generate`:

```bash
okapipy nlp fetch es
okapipy spec parse openapi.yaml --lang es
```

The English heuristic registry (which catches a handful of API verbs that
small spaCy models mistag as nouns — `login`, `logout`, `refresh`, …) is
English-specific. For other languages spaCy's POS tagging carries the
weight, and you can fall back to `x-okapipy-kind: action` when needed.

[pipx]: https://pipx.pypa.io/
[uv]: https://docs.astral.sh/uv/
