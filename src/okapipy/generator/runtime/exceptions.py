"""Exception hierarchy for generated clients.

Generated client code maps every non-2xx httpx response to an `ApiError`
subclass so users have one tree to handle. Strategy errors live alongside —
they share the same root because users will wrap their entire client call site
in `try: ... except ApiError`.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Base for every error raised by the generated client."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request: Any = None,
        response: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request = request
        self.response = response


class ClientError(ApiError):
    """A 4xx response from the server."""


class ServerError(ApiError):
    """A 5xx response from the server."""


class ResponseValidationError(ApiError):
    """A 2xx response body failed Pydantic validation in `models` shape."""


class ConfigurationError(ApiError):
    """A user-supplied strategy or option is invalid."""


class UnsupportedFilterError(ApiError):
    """The configured `FilterStrategy` cannot encode this `Filter` tree."""


class UnsupportedSortError(ApiError):
    """The configured `SortStrategy` cannot encode this `Sort` term list."""


class UnsupportedFilterKeyError(UnsupportedFilterError):
    """A filter key is not declared on the operation."""


class UnsupportedSortFieldError(UnsupportedSortError):
    """A sort field is not declared on the operation."""
