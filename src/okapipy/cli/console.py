"""Shared rich-powered console primitives for the okapipy CLI.

Owns the two `Console` instances (stdout, stderr) used by every subcommand and a
`setup_logging` helper that wires a `RichHandler` onto the `okapipy` logger so that
parser-emitted log records become part of the user-visible output.

Stdout is reserved for machine-readable payloads (JSON dumps); user-facing chrome —
spinners, panels, summary tables, log lines — always goes to stderr so that
`okapipy spec parse … | jq` keeps working.
"""

from __future__ import annotations

import logging
from typing import IO

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.text import Text

LOGGER_NAME = "okapipy"

stdout = Console()
stderr = Console(stderr=True)


class _WarningCounter(logging.Handler):
    """Logging handler that tallies records at WARNING level or above.

    Attached to the okapipy logger alongside the user-facing `RichHandler` so the
    CLI can report a final "N warning(s)" tally without scraping rendered output.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        """Increment the running tally for each WARNING-or-higher record."""
        if record.levelno >= logging.WARNING:
            self.count += 1


_warning_counter = _WarningCounter()


def warnings_emitted() -> int:
    """Return the number of WARNING-or-higher records seen since the last `setup_logging`."""
    return _warning_counter.count


def setup_logging(verbosity: int, *, stderr_console: Console | None = None) -> None:
    """Install a `RichHandler` on the okapipy logger and set its level from `verbosity`.

    Verbosity mapping: 0 → WARNING, 1 → INFO, 2+ → DEBUG. Existing handlers on the
    `okapipy` logger are removed so repeated calls (e.g. across CLI invocations in
    the same process during tests) leave a clean state. The shared warning counter
    is also reset so each invocation reports its own tally.

    Args:
        verbosity: Count of `-v` flags from the CLI.
        stderr_console: Optional override used by tests to capture rendered output.
    """
    target = stderr_console if stderr_console is not None else stderr
    level = level_for(verbosity)
    handler = RichHandler(
        console=target,
        show_time=False,
        show_path=verbosity >= 2,
        rich_tracebacks=verbosity >= 2,
        markup=False,
        log_time_format="[%X]",
    )
    handler.setLevel(level)
    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        if not isinstance(existing, _WarningCounter):
            logger.removeHandler(existing)
    logger.addHandler(handler)
    if _warning_counter not in logger.handlers:
        logger.addHandler(_warning_counter)
    _warning_counter.count = 0
    logger.setLevel(logging.DEBUG)


def print_error(
    exc: BaseException,
    *,
    debug: bool,
    console: Console | None = None,
) -> None:
    """Render `exc` as a red panel on the stderr console.

    With `debug=True` a rich-formatted traceback is also printed so users running
    with `-vv` can diagnose unexpected failures.
    """
    target = console if console is not None else stderr
    body = Text(f"{type(exc).__name__}: {exc}", style="bold")
    target.print(Panel(body, title="Error", border_style="red", title_align="left"))
    if debug:
        target.print_exception(show_locals=False)


def is_piped(console: Console | None = None) -> bool:
    """Return True when stdout is not connected to an interactive terminal."""
    target = console if console is not None else stdout
    return not target.is_terminal


def write_stream(text: str, *, file: IO[str]) -> None:
    """Write `text` to a raw file stream without rich decoration.

    Used when the caller needs a guaranteed plain payload (e.g. piped JSON).
    """
    file.write(text)
    if not text.endswith("\n"):
        file.write("\n")
    file.flush()


def level_for(verbosity: int) -> int:
    """Map a verbosity count to a stdlib logging level."""
    if verbosity <= 0:
        return logging.WARNING
    if verbosity == 1:
        return logging.INFO
    return logging.DEBUG
