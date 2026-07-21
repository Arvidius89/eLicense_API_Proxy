import json
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import Response

from proxy.auth import require_api_key
from proxy.errors import (
    ProxyError,
    http_exception_handler,
    proxy_error_handler,
    unhandled_exception_handler,
)
from proxy.logging_config import logger
from proxy.proxy_client import forward_request
from proxy.settings import AppSettings, get_settings_or_raise, load_settings

app = FastAPI(
    title="eLicense IA Proxy",
    description="Secure FastAPI proxy for IA API with inbound API key auth and outbound mTLS.",
    version="1.0.0",
)

app.add_exception_handler(ProxyError, proxy_error_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    request.state.started_at = time.perf_counter()

    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.on_event("startup")
async def startup_event() -> None:
    try:
        app.state.settings = load_settings()
        app.state.startup_error = None
        logger.info(
            "Application started",
            extra={
                "event": "startup",
            },
        )
    except ProxyError as exc:
        app.state.settings = None
        app.state.startup_error = exc
        logger.error(
            "Startup configuration error",
            extra={
                "event": "startup_error",
            },
        )


async def read_json_body(request: Request) -> bytes:
    body = await request.body()
    try:
        json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProxyError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_json",
            message="Request body must be valid JSON",
        ) from exc
    return body


@app.get("/health", tags=["System"])
async def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post(
    "/documents",
    dependencies=[Depends(require_api_key)],
    tags=["Documents"],
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "documents": [{"docType": "org.iso.23220.1.nl.kiwa.sampcert", "document_number": "Kiwa_260303_1"}],
                        "license_holder": {"last_name": "Jansen", "first_name": "Piet", "email": "piet.jansen@example.com"},
                    }
                }
            }
        }
    },
)
async def create_documents(request: Request, settings: AppSettings = Depends(get_settings_or_raise)) -> Response:
    body = await read_json_body(request)
    return await forward_request(
        request=request,
        method="POST",
        backend_path="/ia/v2/documents",
        settings=settings,
        body=body,
    )


@app.post(
    "/documents/activation-status",
    dependencies=[Depends(require_api_key)],
    tags=["Documents"],
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": [
                        {"docType": "taxivergunning.1", "document_number": "P123456/T/TT/1234567"},
                        {"docType": "hefcert.leus.1", "document_number": "20000"},
                    ]
                }
            }
        }
    },
)
async def activation_status(request: Request, settings: AppSettings = Depends(get_settings_or_raise)) -> Response:
    body = await read_json_body(request)
    return await forward_request(
        request=request,
        method="POST",
        backend_path="/ia/documents/activation-status",
        settings=settings,
        body=body,
    )


@app.delete(
    "/documents",
    dependencies=[Depends(require_api_key)],
    tags=["Documents"],
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": [
                        {"docType": "org.iso.23220.1.nl.kiwa.sampcert", "document_number": "Kiwa_260303_1"}
                    ]
                }
            }
        },
        "parameters": [
            {
                "name": "docType",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": "org.iso.23220.1.nl.kiwa.sampcert",
            },
            {
                "name": "document_number",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": "Kiwa_260303_1",
            },
        ]
    },
)
async def delete_documents(request: Request, settings: AppSettings = Depends(get_settings_or_raise)) -> Response:
    body = await request.body()
    query_params = list(request.query_params.multi_items())
    return await forward_request(
        request=request,
        method="DELETE",
        backend_path="/ia/documents",
        settings=settings,
        body=body,
        extra_query=query_params,
    )
