"""Sentinels and shared dataclasses used by the runtime.

`UNSET` distinguishes "user did not pass the kwarg → use client default" from
"user passed `None` → disable the option for this call". `RequestOptions` carries
the cross-cutting per-request overrides accumulated by the collection's
`with_options(**...)` method.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final


class Unset:
    """Singleton type for the `UNSET` sentinel.

    Implemented as a class so callers can write `Unset` in type annotations
    (`timeout: float | None | Unset = UNSET`). The class has only one instance.
    """

    _instance: Unset | None = None

    def __new__(cls) -> Unset:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Final[Unset] = Unset()


@dataclass(frozen=True)
class RequestOptions:
    """Per-collection cross-cutting overrides set by `with_options(**...)`.

    `params` and `headers` are merged into every request the collection issues.
    The other fields replace the client-level defaults; the `UNSET` sentinel means
    "fall back to the client default".
    """

    params: Mapping[str, Any] | None = None
    headers: Mapping[str, str] | None = None
    timeout: Any = field(default=UNSET)
    auth: Any = field(default=UNSET)
    verify: Any = field(default=UNSET)
    retries: Any = field(default=UNSET)
