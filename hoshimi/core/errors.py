"""Domain exceptions shared by the API and the command line."""

from typing import Any


class HoshimiError(Exception):
    """Base class for every error the wrappers translate."""

    code = "internal_error"
    http_status = 500
    exit_code = 1

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class NotFoundError(HoshimiError):
    """A requested object does not exist."""

    code = "not_found"
    http_status = 404
    exit_code = 2


class ModelNotFoundError(NotFoundError):
    """No enabled provider serves the requested model."""

    code = "model_not_found"


class ConflictError(HoshimiError):
    """The object is in a state that forbids the operation."""

    code = "conflict"
    http_status = 409
    exit_code = 3


class ValidationError(HoshimiError):
    """An input value is not acceptable."""

    code = "invalid_input"
    http_status = 400
    exit_code = 4


class UpstreamError(HoshimiError):
    """A model station failed, refused the request or is unreachable."""

    code = "upstream_error"
    http_status = 502
    exit_code = 5


class ModelUnavailableError(UpstreamError):
    """The station stopped answering for this id, so the gateway did not ask.

    A 404 from every account is a statement about the model, not about the
    caller; asking again spends a round trip to learn the same thing. The ban
    lifts on its own (see :class:`hoshimi.core.failure.DeadModelStore`), so the
    answer is "not now" rather than "not ever" — 503 rather than 404.
    """

    code = "model_unavailable"
    http_status = 503
