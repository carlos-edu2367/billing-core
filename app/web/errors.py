import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import DomainError, NotFoundError

logger = logging.getLogger(__name__)


def _error_response(request: Request, status_code: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    content = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    return JSONResponse(status_code=status_code, content=content, headers=headers)


def register_request_too_large(request_id: str | None) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        content={
            "error": {
                "code": "payload_too_large",
                "message": "Payload excede o tamanho maximo permitido.",
                "request_id": request_id,
            }
        },
    )


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        logger.warning(
            "HTTP exception raised",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_detail": str(exc.detail),
            },
        )
        code = {
            status.HTTP_400_BAD_REQUEST: "bad_request",
            status.HTTP_401_UNAUTHORIZED: "unauthorized",
            status.HTTP_403_FORBIDDEN: "forbidden",
            status.HTTP_404_NOT_FOUND: "not_found",
            status.HTTP_409_CONFLICT: "conflict",
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
            status.HTTP_429_TOO_MANY_REQUESTS: "rate_limit_exceeded",
            status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
        }.get(exc.status_code, "http_error")
        return _error_response(request, exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(NotFoundError)
    async def handle_not_found(request: Request, exc: NotFoundError):
        logger.info(
            "Not found",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "error_code": exc.__class__.__name__,
            },
        )
        return _error_response(request, status.HTTP_404_NOT_FOUND, "not_found", str(exc))

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError):
        logger.warning(
            "Domain error",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "error_code": exc.__class__.__name__,
                "error_message": str(exc),
            },
        )
        return _error_response(request, status.HTTP_400_BAD_REQUEST, "domain_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        logger.warning(
            "Validation error",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "errors": exc.errors(),
            },
        )
        return _error_response(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "Dados invalidos.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        logger.exception(
            "Unhandled exception",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "path": request.url.path,
                "error_code": exc.__class__.__name__,
            },
        )
        return _error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_server_error",
            "Erro interno.",
        )
