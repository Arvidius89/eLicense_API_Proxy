import uuid

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

from .logging_config import logger


class ProxyError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)
async def proxy_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ProxyError):
        return await unhandled_exception_handler(request, exc)

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": request_id,
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception(
        "Unexpected exception",
        extra={
            "event": "unexpected_exception",
            "request_id": request_id,
            "endpoint": request.url.path,
            "http_method": request.method,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "unexpected_error",
                "message": "An unexpected error occurred",
                "request_id": request_id,
            }
        },
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):
        return await unhandled_exception_handler(request, exc)

    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "http_error",
                "message": str(exc.detail),
                "request_id": request_id,
            }
        },
    )
