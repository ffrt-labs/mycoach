"""Global exception handlers for the FastAPI application."""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors: list[dict[str, Any]] = []
        for error in exc.errors():
            loc = " → ".join(str(part) for part in error["loc"])
            errors.append({"field": loc, "message": error["msg"]})
        logger.warning(
            "Validation error on %s %s: %s",
            request.method,
            request.url.path,
            errors,
        )
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": "Request validation failed",
                "errors": errors,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "http_error",
                "detail": exc.detail,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled exception on %s %s",
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "An unexpected error occurred. Please try again later.",
            },
        )
