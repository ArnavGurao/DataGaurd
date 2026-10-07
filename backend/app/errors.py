"""One structured error shape for every failure (README 9.7).

Every error response — raised deliberately, rejected by request validation, or
an unexpected crash — serializes as::

    {"error": {"code": "FILE_TOO_LARGE", "message": "Upload a CSV of 5 MiB or less."}}

Keeping this uniform means the frontend helper has exactly one shape to parse.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("dataguard.errors")

# Application error codes. Values are stable identifiers the UI may branch on.
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_CONFLICT = "CONFLICT"
CODE_RULE_SET_INVALID = "RULE_SET_INVALID"
CODE_REPORT_NOT_READY = "REPORT_NOT_READY"
CODE_FILE_TOO_LARGE = "FILE_TOO_LARGE"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
CODE_STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"
CODE_METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"

#: Default code per HTTP status when a plain HTTPException is raised.
_STATUS_CODES: dict[int, str] = {
    400: CODE_VALIDATION_ERROR,
    401: CODE_UNAUTHORIZED,
    403: CODE_UNAUTHORIZED,
    404: CODE_NOT_FOUND,
    405: CODE_METHOD_NOT_ALLOWED,
    409: CODE_CONFLICT,
    413: CODE_FILE_TOO_LARGE,
    422: CODE_VALIDATION_ERROR,
    503: CODE_STORAGE_UNAVAILABLE,
}


class ApiError(Exception):
    """An error we raise on purpose, carrying its own status and code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _json(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, message))


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so no route can return an out-of-shape error body."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _json(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Condense Pydantic's list into one readable sentence; the frontend
        # shows a single message, not a field-by-field report.
        details = []
        for issue in exc.errors():
            location = ".".join(str(part) for part in issue.get("loc", ()) if part != "body")
            details.append(f"{location}: {issue.get('msg', 'invalid value')}" if location else issue.get("msg", "invalid value"))
        message = "Invalid request. " + "; ".join(details) if details else "Invalid request."
        return _json(422, CODE_VALIDATION_ERROR, message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, CODE_INTERNAL_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _json(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Log the traceback server-side; never return it to the client
        # (README 9.2 forbids leaking internals through responses).
        logger.exception("Unhandled error", exc_info=exc)
        return _json(500, CODE_INTERNAL_ERROR, "The server could not complete the request.")
