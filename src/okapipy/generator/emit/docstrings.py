"""Class- and accessor-docstring builders for every generated artifact.

The emitters hand parser nodes to this module and receive triple-quoted
blocks ready to splice into a `.py.jinja` template. Each builder formats a
lead paragraph from the spec's `summary`/`description`, then (for class
docstrings) appends a markdown map of children and operations so the IDE
tooltip reads as a self-contained reference.

Bullet rendering is shared via `_render_bullet`. Class docstrings reuse
`_compose_class_doc_body` so every emitter produces the same section
shape; differences live in which sections each node kind contributes.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass

from okapipy.parser.model import (
    Action,
    Collection,
    Namespace,
    Operation,
    Resource,
    Singleton,
)


@dataclass(frozen=True)
class ChildRef:
    """Reference to a child node — used in template contexts for property emission.

    `docstring` is the fully-formatted triple-quoted block spliced into the
    property accessor. `one_line` and `meta_inline` carry the short bullet
    text used in the *parent class*'s docstring map (the section that lists
    every reachable child); they are unused at the property-accessor site.
    """

    attr: str  # snake_case property name
    class_name: str  # PascalCase class name including the `Base` suffix
    module: str  # snake_case module name (no suffix, no extension)
    factory_attr: str  # dunder-protected ClassVar hook, e.g. `__orders_factory__`
    docstring: str | None = None  # docstring for the property accessor (indent=8)
    one_line: str = ""  # short description used in the parent class's docstring map
    meta_inline: str = (
        ""  # optional inline meta in the bullet (e.g. "`POST /admin/reindex`")
    )


def build_docstring(
    summary: str | None,
    description: str | None,
    fallback: str,
    indent: int = 4,
) -> str:
    """Format a Python docstring from OpenAPI `summary` + `description`.

    Returns a triple-quoted block ready to splice into a generated source file.
    `indent` controls left padding (4 for class docstrings, 8 for method
    docstrings). Falls back to `fallback` when both inputs are empty.
    """
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if description and description.strip():
        parts.append(description.strip())
    body = "\n\n".join(parts) if parts else fallback
    return _build_docstring_from_body(body, indent)


def collection_property_docstring(coll: Collection, indent: int = 8) -> str | None:
    """Build the docstring for a property that exposes a `Collection`.

    The accessor (e.g. `Admin.accounts`, `Order.lines`, `client.orders`)
    inherits the collection's `fetch` operation docs so the call site shows
    the collection's purpose without forcing the user to navigate into the
    collection class. When fetch has no documentation, fall back to the
    collection's own summary/description, and finally to a structural
    string identifying the path.
    """
    fetch = coll.fetch
    if fetch is not None:
        return build_docstring(
            fetch.summary,
            fetch.description,
            fallback=f"Collection at `{coll.path}`.",
            indent=indent,
        )
    return build_docstring(
        coll.summary,
        coll.description,
        fallback=f"Collection at `{coll.path}`.",
        indent=indent,
    )


def build_action_docstring(action: Action, indent: int = 4) -> str:
    """Format an action class docstring: single-op uses the op's text, multi-op lists them.

    Multi-op actions render an `#### Operations` section with one bullet per
    HTTP verb so the IDE tooltip reads as a self-contained map. Single-op
    actions reuse the operation's own summary/description because the class
    and the only method are documenting the same thing.
    """
    if not action.operations:
        return build_docstring(
            action.summary,
            action.description,
            f"Action at `{action.path}`.",
            indent,
        )
    if len(action.operations) == 1:
        op = action.operations[0]
        return build_docstring(
            op.summary,
            op.description,
            f"Action at `{action.path}`.",
            indent,
        )
    header: list[str] = []
    if action.summary and action.summary.strip():
        header.append(action.summary.strip())
    elif action.description and action.description.strip():
        header.append(action.description.strip())
    else:
        header.append(f"Action at `{action.path}`.")
    header.append("")
    header.append("#### Operations")
    header.append("")
    for op in action.operations:
        summary = (op.summary or "").strip() or "(no summary)"
        header.append(f"- `{op.method} {action.path}` — {summary}")
    return _build_docstring_from_body("\n".join(header), indent)


def build_client_class_docstring(
    *,
    project_name: str,
    project_version: str | None,
    sync: bool,
    top_namespaces: Sequence[ChildRef],
    top_collections: Sequence[ChildRef],
    top_singletons: Sequence[ChildRef],
    top_actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Render the IDE-facing class docstring for the client base class.

    The body opens with a one-line lead identifying the project and shape,
    then lists each top-level child kind that is populated. Sections are
    omitted when their child list is empty so a tiny spec doesn't produce a
    docstring full of empty headers.

    `sync=True` produces the `<Client>Base` flavor; `sync=False` produces
    the async sibling text. Both flavors share the same map — only the lead
    differs.
    """
    if sync:
        title = f"HTTP client for `{project_name}`"
    else:
        title = f"Asynchronous HTTP client for `{project_name}`"
    if project_version:
        title += f" (v{project_version})."
    else:
        title += "."
    lead_lines = [
        title,
        "",
        "Construct with `base_url=...`. Configure pagination, filter, and sort",
        "strategies via the matching keyword arguments.",
    ]
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    if top_collections:
        sections.append(("Top-level collections", top_collections))
    if top_singletons:
        sections.append(("Top-level singletons", top_singletons))
    if top_namespaces:
        sections.append(("Top-level namespaces", top_namespaces))
    if top_actions:
        sections.append(("Top-level actions", top_actions))
    body = _compose_class_doc_body(lead="\n".join(lead_lines), sections=sections)
    return _build_docstring_from_body(body, indent)


def namespace_accessor_docstring(ns: Namespace, indent: int = 8) -> str:
    """Return the property-accessor docstring for a namespace (always non-None).

    A short one-liner so the IDE popup stays compact. We deliberately do
    not name the namespace class here because the same string is reused
    for the sync and async accessors; pinning a class name would mislead
    one of the two readers.
    """
    summary = node_one_line(
        ns.summary, ns.description, fallback=f"Namespace `{ns.name}`."
    )
    return _build_docstring_from_body(summary, indent)


def singleton_accessor_docstring(singleton: Singleton, indent: int = 8) -> str:
    """Return the property-accessor docstring for a singleton (always non-None)."""
    fallback = f"Singleton at `{singleton.path}`."
    candidate_summary = singleton.summary
    candidate_description = singleton.description
    if (
        not (candidate_summary or candidate_description)
        and singleton.retrieve is not None
    ):
        candidate_summary = singleton.retrieve.summary
        candidate_description = singleton.retrieve.description
    summary = node_one_line(candidate_summary, candidate_description, fallback=fallback)
    return _build_docstring_from_body(summary, indent)


def action_accessor_docstring(action: Action, indent: int = 8) -> str:
    """Return the property-accessor docstring for an action (always non-None)."""
    candidate_summary = action.summary
    candidate_description = action.description
    if not (candidate_summary or candidate_description) and action.operations:
        candidate_summary = action.operations[0].summary
        candidate_description = action.operations[0].description
    fallback = f"Action at `{action.path}`."
    summary = node_one_line(candidate_summary, candidate_description, fallback=fallback)
    if len(action.operations) == 1:
        op = action.operations[0]
        body = f"`{op.method} {action.path}`. {summary}"
    else:
        body = summary
    return _build_docstring_from_body(body, indent)


def getitem_accessor_docstring(*, id_param: str, indent: int = 8) -> str:
    """Return the docstring for `Collection.__getitem__` (always non-None).

    Indexing is request-free — the resource is constructed lazily and
    only issues a request when one of its CRUD methods is called. The
    docstring is sync/async-agnostic; the actual return type comes from
    the property annotation, which already encodes the correct sibling.
    """
    body = (
        f"Address one item by `{id_param}`. No HTTP call until a CRUD "
        f"method runs on the returned resource."
    )
    return _build_docstring_from_body(body, indent)


def build_namespace_class_docstring(
    ns: Namespace,
    *,
    child_namespaces: Sequence[ChildRef],
    child_collections: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    child_actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Lead paragraph from `ns.summary`/`ns.description`, then a map of children."""
    fallback = f"Namespace router for `{ns.name}`."
    lead = _lead_paragraph(ns.summary, ns.description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    if child_namespaces:
        sections.append(("Sub-namespaces", child_namespaces))
    if child_collections:
        sections.append(("Collections", child_collections))
    if child_singletons:
        sections.append(("Singletons", child_singletons))
    if child_actions:
        sections.append(("Actions", child_actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def build_collection_class_docstring(
    coll: Collection,
    *,
    resource_ref: ChildRef | None,
    actions: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    create_op: Operation | None,
    indent: int = 4,
) -> str:
    """Compose the collection docstring from fetch op + item / ops / sub-singletons / actions.

    `Operations on the collection` always lists the standard query helpers
    (`first`, `count`, `exists`, iteration), plus `.get_page(n)` when
    pagination is supported (`coll.fetch.pagination_supported`); the
    `create(body)` bullet is added only when the parser populated
    `Collection.create`. `Item access` is omitted when there is no resource
    child. `Sub-singletons` appears when the collection hosts aggregate-view
    singletons such as `/orders/stats`.
    """
    fallback = f"Collection at `{coll.path}`."
    if coll.fetch is not None:
        lead = _lead_paragraph(coll.fetch.summary, coll.fetch.description, fallback)
    else:
        lead = _lead_paragraph(coll.summary, coll.description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    if resource_ref is not None:
        sections.append(("Item access", [resource_ref]))
    sections.append(
        ("Operations on the collection", _collection_operation_bullets(coll, create_op))
    )
    if child_singletons:
        sections.append(("Sub-singletons", child_singletons))
    if actions:
        sections.append(("Actions", actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def build_resource_class_docstring(
    resource: Resource,
    *,
    child_collections: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Lead from `resource.summary`/`description`, then CRUD / sub-trees / actions."""
    fallback = f"Resource at `{resource.path}`."
    lead = _lead_paragraph(resource.summary, resource.description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    crud = _crud_bullets(
        path=resource.path,
        retrieve=resource.retrieve,
        update=resource.update,
        partial_update=resource.partial_update,
        delete=resource.delete,
    )
    if crud:
        sections.append(("Operations", crud))
    if child_collections:
        sections.append(("Sub-collections", child_collections))
    if child_singletons:
        sections.append(("Sub-singletons", child_singletons))
    if actions:
        sections.append(("Actions", actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def build_singleton_class_docstring(
    singleton: Singleton,
    *,
    child_collections: Sequence[ChildRef],
    child_singletons: Sequence[ChildRef],
    actions: Sequence[ChildRef],
    indent: int = 4,
) -> str:
    """Same shape as the resource builder, minus `[id]` indexing."""
    fallback = f"Singleton at `{singleton.path}`."
    candidate_summary = singleton.summary
    candidate_description = singleton.description
    if (
        not (candidate_summary or candidate_description)
        and singleton.retrieve is not None
    ):
        candidate_summary = singleton.retrieve.summary
        candidate_description = singleton.retrieve.description
    lead = _lead_paragraph(candidate_summary, candidate_description, fallback)
    sections: list[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]] = []
    crud = _crud_bullets(
        path=singleton.path,
        retrieve=singleton.retrieve,
        update=singleton.update,
        partial_update=singleton.partial_update,
        delete=singleton.delete,
    )
    if crud:
        sections.append(("Operations", crud))
    if child_collections:
        sections.append(("Sub-collections", child_collections))
    if child_singletons:
        sections.append(("Sub-singletons", child_singletons))
    if actions:
        sections.append(("Actions", actions))
    body = _compose_class_doc_body(lead=lead, sections=sections)
    return _build_docstring_from_body(body, indent)


def node_one_line(
    summary: str | None,
    description: str | None,
    *,
    fallback: str,
) -> str:
    """Return a single-line summary suitable for a bullet's `one_line` slot.

    Picks the first non-empty source (summary, description, fallback),
    keeps only its first line, and trims to the first sentence-terminator
    (`.`, `?`, `!`) when one occurs in the first ~120 characters. The
    result is *not* punctuated automatically — the bullet renderer adds a
    period only when none of the source text already ends with one.
    """
    raw = (summary or "").strip() or (description or "").strip() or fallback.strip()
    first_line = raw.split("\n", 1)[0].strip()
    if not first_line:
        return fallback.strip()
    for terminator in (". ", "? ", "! "):
        idx = first_line.find(terminator)
        if idx != -1 and idx < 120:
            return first_line[: idx + 1]
    if len(first_line) > 200:
        return first_line[:200].rstrip() + "…"
    return first_line


def collection_one_line(coll: Collection) -> str:
    """One-liner for a collection bullet — falls back to the fetch op's summary."""
    if coll.summary or coll.description:
        return node_one_line(
            coll.summary,
            coll.description,
            fallback=f"Collection at `{coll.path}`.",
        )
    if coll.fetch is not None:
        return node_one_line(
            coll.fetch.summary,
            coll.fetch.description,
            fallback=f"Collection at `{coll.path}`.",
        )
    return f"Collection at `{coll.path}`."


def singleton_one_line(singleton: Singleton) -> str:
    """One-liner for a singleton bullet — falls back to the retrieve op's summary."""
    if singleton.summary or singleton.description:
        return node_one_line(
            singleton.summary,
            singleton.description,
            fallback=f"Singleton at `{singleton.path}`.",
        )
    if singleton.retrieve is not None:
        return node_one_line(
            singleton.retrieve.summary,
            singleton.retrieve.description,
            fallback=f"Singleton at `{singleton.path}`.",
        )
    return f"Singleton at `{singleton.path}`."


def action_meta_inline(action: Action) -> str:
    """Inline meta string for an action when it appears as a bullet under a parent."""
    if len(action.operations) == 1:
        op = action.operations[0]
        return f"`{op.method} {action.path}`"
    if action.operations:
        return f"`{action.path}` (multiple ops)"
    return f"`{action.path}`"


def action_one_line(action: Action) -> str:
    """One-liner for an action bullet — falls back to the only op's summary."""
    if action.summary or action.description:
        return node_one_line(
            action.summary, action.description, fallback=f"Action at `{action.path}`."
        )
    if len(action.operations) == 1:
        op = action.operations[0]
        return node_one_line(
            op.summary, op.description, fallback=f"Action at `{action.path}`."
        )
    return f"Action at `{action.path}`."


@dataclass(frozen=True)
class _StaticBullet:
    """A non-child bullet for a class-docstring section.

    Used to render entries that don't correspond to a child node — e.g.
    `.first()` / `.count()` on a collection, the inline `for item in
    collection:` iteration hint, or the `.create(body)` line whose
    method/path comes from the collection's create op.
    """

    label: str  # rendered as **`label`**
    one_line: str = ""
    meta_inline: str = ""


def _build_docstring_from_body(
    body: str, indent: int, max_line_length: int = 100
) -> str:
    """Wrap `body` in a triple-quoted block at the given indent level.

    Long prose paragraphs (e.g. an OpenAPI `description` spliced verbatim
    into the body) are reflowed to fit `max_line_length` once the leading
    pad and `\"\"\"` overhead are accounted for. Markdown bullets keep a
    hanging two-space indent on continuation lines so they still render
    as a single bullet; section headings (`#### …`) and pre-broken lines
    in the body are preserved verbatim.
    """
    body = body.replace('"""', "'''")
    pad = " " * indent
    # Subtract 6 to fit the worst case `pad + """body"""` form: ruff's
    # docstring formatter collapses a fitting multi-line block onto one
    # line, and the closing triple-quote then adds 3 chars on top of the
    # opening 3. Wrapping more conservatively keeps the post-format step
    # honest with its own line-length lint.
    wrap_width = max(20, max_line_length - indent - 6)
    body = _wrap_docstring_body(body, wrap_width)
    lines = body.split("\n")
    if len(lines) == 1 and len(lines[0]) + indent + 6 <= max_line_length:
        return f'{pad}"""{lines[0]}"""'
    first = lines[0]
    rest = lines[1:]
    out_lines = [f'{pad}"""{first}']
    for line in rest:
        out_lines.append(pad + line if line.strip() else "")
    out_lines.append(f'{pad}"""')
    return "\n".join(out_lines)


def _wrap_docstring_body(body: str, width: int) -> str:
    """Reflow `body` so each line fits within `width` columns.

    Walks the body line by line. Consecutive non-empty, non-structural
    lines are grouped into a single paragraph and rewrapped together.
    Bullet lines (starting with `- `) are wrapped with a hanging
    two-space indent so the bullet structure survives. Section headings
    (`#### …`) and blank lines are preserved verbatim.
    """
    out: list[str] = []
    paragraph: list[str] = []

    def _flush_paragraph() -> None:
        if not paragraph:
            return
        text = " ".join(line.strip() for line in paragraph if line.strip())
        if text:
            out.append(
                textwrap.fill(
                    text,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
            )
        paragraph.clear()

    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            _flush_paragraph()
            out.append("")
            continue
        if stripped.startswith("####"):
            _flush_paragraph()
            out.append(line)
            continue
        if stripped.startswith("- "):
            _flush_paragraph()
            out.append(
                textwrap.fill(
                    line,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                    subsequent_indent="  ",
                )
            )
            continue
        paragraph.append(line)
    _flush_paragraph()

    return "\n".join(out)


def _compose_class_doc_body(
    *,
    lead: str,
    sections: Sequence[tuple[str, Sequence[ChildRef] | Sequence[_StaticBullet]]],
) -> str:
    """Stitch a class-docstring body: lead paragraph + non-empty markdown sections."""
    chunks: list[str] = [lead.rstrip()]
    for title, items in sections:
        if not items:
            continue
        chunks.append("")
        chunks.append(f"#### {title}")
        chunks.append("")
        for item in items:
            chunks.append(_render_bullet(item))
    return "\n".join(chunks)


def _render_bullet(entry: ChildRef | _StaticBullet) -> str:
    """Format a single section bullet in markdown.

    Both `ChildRef` and `_StaticBullet` may carry an optional `meta_inline`
    (rendered as backticked code, e.g. `` `POST /admin/reindex` ``) and a
    `one_line` (the short prose). When both are present we join them with a
    period; when neither is present we emit just the head.
    """
    if isinstance(entry, ChildRef):
        head = f"- **`{entry.attr}`** → `{entry.class_name}`"
    else:
        head = f"- **`{entry.label}`**"
    bits = [piece for piece in (entry.meta_inline, entry.one_line) if piece]
    if not bits:
        return head
    rhs = ". ".join(bits)
    if not rhs.endswith((".", "?", "!")):
        rhs += "."
    return f"{head} — {rhs}"


def _lead_paragraph(summary: str | None, description: str | None, fallback: str) -> str:
    """Lead-paragraph composition shared by every class-docstring builder.

    Same precedence as `build_docstring` (summary, then description, then
    fallback) but always yields a non-empty string and never includes
    section headers — the caller appends those.
    """
    parts: list[str] = []
    if summary and summary.strip():
        parts.append(summary.strip())
    if description and description.strip():
        parts.append(description.strip())
    if not parts:
        return fallback
    return "\n\n".join(parts)


def _collection_operation_bullets(
    coll: Collection, create_op: Operation | None
) -> list[_StaticBullet]:
    """Build the `#### Operations on the collection` bullets.

    Lists the standard query helpers plus a `.create(body)` line when the
    parser populated a create operation. Iteration is folded in as a
    structural hint rather than a method call so users see the `for`
    pattern at a glance. When the fetch operation has
    `pagination_supported=False`, the strategy-driven entries (`.get_page(n)`
    and the "paginated" hint) are replaced with a single-fetch hint and
    `.get_page(n)` is dropped entirely.
    """
    paginated = coll.fetch is None or coll.fetch.pagination_supported
    bullets: list[_StaticBullet] = []
    if create_op is not None:
        bullets.append(
            _StaticBullet(
                label=".create(body)",
                meta_inline=f"`{create_op.method} {coll.path}`",
                one_line=node_one_line(
                    create_op.summary,
                    create_op.description,
                    fallback="Create a new item.",
                ),
            )
        )
    bullets.append(
        _StaticBullet(label=".first()", one_line="Return the first item, or `None`.")
    )
    if paginated:
        bullets.append(
            _StaticBullet(
                label=".count()",
                one_line=(
                    "Return the total count via the configured pagination strategy"
                ),
            )
        )
    else:
        bullets.append(
            _StaticBullet(
                label=".count()",
                one_line="Return the size of a single fetch (no wire pagination)",
            )
        )
    bullets.append(
        _StaticBullet(label=".exists()", one_line="Equivalent to `count() > 0`")
    )
    if paginated:
        bullets.append(
            _StaticBullet(
                label=".get_page(n)",
                one_line=(
                    "Fetch a single 0-indexed page (offset/page-number strategies only)"
                ),
            )
        )
        iter_hint = "Paginated iteration"
    else:
        iter_hint = "Single-fetch iteration"
    bullets.append(
        _StaticBullet(
            label="for item in collection: ...",
            one_line=iter_hint,
        )
    )
    return bullets


def _crud_bullets(
    *,
    path: str,
    retrieve: Operation | None,
    update: Operation | None,
    partial_update: Operation | None,
    delete: Operation | None,
) -> list[_StaticBullet]:
    """Bullets for the CRUD methods a Resource or Singleton actually exposes.

    A method that the spec did not declare is silently dropped — listing it
    in the docstring would surface a method that doesn't exist on the
    class.
    """
    bullets: list[_StaticBullet] = []
    pairings: tuple[tuple[str, Operation | None, str], ...] = (
        (".retrieve()", retrieve, "Fetch the item."),
        (".update(body)", update, "Replace the item."),
        (".patch(body)", partial_update, "Modify selected fields."),
        (".delete()", delete, "Delete the item."),
    )
    for label, op, fallback in pairings:
        if op is None:
            continue
        bullets.append(
            _StaticBullet(
                label=label,
                meta_inline=f"`{op.method} {path}`",
                one_line=node_one_line(op.summary, op.description, fallback=fallback),
            )
        )
    return bullets
