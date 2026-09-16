"""Domain and application errors.

Every error carries a stable machine-readable ``code``. The API layer maps error classes to HTTP status
codes; the domain itself knows nothing about HTTP.
"""

from typing import Any


class DomainError(Exception):
    code: str = "DOMAIN_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code is not None:
            self.code = code


class NotFoundError(DomainError):
    code = "NOT_FOUND"


class ValidationFailedError(DomainError):
    """Supplied information is syntactically valid but inconsistent with reference data."""

    code = "VALIDATION_FAILED"


class InvalidStateTransitionError(DomainError):
    code = "INVALID_STATE_TRANSITION"


class OperationNotAllowedError(DomainError):
    """The operation is not permitted for the case in its current state or context."""

    code = "OPERATION_NOT_ALLOWED"


class AuthorizationError(DomainError):
    code = "FORBIDDEN"


class ConcurrencyConflictError(DomainError):
    code = "CONCURRENT_MODIFICATION"


class IntegrityViolationError(DomainError):
    """An internal invariant was violated. Indicates a defect, never a user error."""

    code = "INTEGRITY_VIOLATION"
