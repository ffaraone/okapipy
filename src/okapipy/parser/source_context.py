"""Source tagging for parser log messages during multi-spec generation.

The parser emits WARNING / INFO records as it walks a spec — "skipping
path X", "all paths share prefix Y", etc. When several specs parse in
the same run (as in `okapipy.generator.compose.plan_mounts`), those
records arrive interleaved and the user can't tell which spec each one
came from.

This module ships a `ContextVar`-backed `source_context(...)` context
manager. The caller wraps each `parse(...)` invocation with the
manifest's mount name; a `LogRecord` factory installed at module
import picks the value up at record-creation time and prepends
`[<mount>]` to every record whose logger name starts with
`okapipy.parser`. When no context is active (e.g. single-spec
`okapipy parse`), the factory is a no-op.

A record-factory hook is used rather than a `logging.Filter`: filters
attached to a logger only run for records originating at that logger,
not for records propagated up from child loggers (per Python's
logging docs). The factory runs at record creation, so it tags every
`okapipy.parser.*` submodule's output uniformly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_PARSER_LOGGER_PREFIX = "okapipy.parser"

_current_source: ContextVar[str | None] = ContextVar(
    "okapipy_parser_source", default=None
)


def _install_record_factory() -> None:
    """Wrap the current `LogRecordFactory` with a parser-tagging variant.

    Calling this twice is a no-op: the wrapper sets a marker attribute
    on itself so reimports don't stack multiple layers.
    """
    factory = logging.getLogRecordFactory()
    if getattr(factory, "_okapipy_source_tagging", False):
        return

    def _tagging_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = factory(*args, **kwargs)
        source = _current_source.get()
        if source and record.name.startswith(_PARSER_LOGGER_PREFIX):
            # Resolve `record.msg % record.args` first so a `%` in the
            # source tag (URLs, query strings) cannot collide with the
            # format placeholders the caller used.
            record.msg = f"[{source}] {record.getMessage()}"
            record.args = ()
        return record

    _tagging_factory._okapipy_source_tagging = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_tagging_factory)


_install_record_factory()


@contextmanager
def source_context(source: str) -> Iterator[None]:
    """Tag every parser log record emitted in the block with `[<source>]`.

    Args:
        source: A short identifier — typically the manifest mount name
            (e.g. `"auth"`, `"restapi"`, or `"root"` for the empty
            mount). Anything that uniquely identifies the spec within
            the run is fine.
    """
    token = _current_source.set(source)
    try:
        yield
    finally:
        _current_source.reset(token)
