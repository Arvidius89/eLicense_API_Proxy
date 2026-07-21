import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader


load_dotenv()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        if hasattr(record, "http_method"):
            payload["http_method"] = record.http_method
        if hasattr(record, "endpoint"):
            payload["endpoint"] = record.endpoint
        if hasattr(record, "backend_endpoint"):
            payload["backend_endpoint"] = record.backend_endpoint
        if hasattr(record, "response_status"):
            payload["response_status"] = record.response_status
        if hasattr(record, "backend_duration_ms"):
            payload["backend_duration_ms"] = record.backend_duration_ms
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = record.duration_ms
        return json.dumps(payload)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("proxy")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonFormatter())
    logger.addHandler(stream_handler)

    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(logs_dir / "proxy.log")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


logger = configure_logging()


class ProxyError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class AppSettings:
    base_url_ia: str
    subscription_key: str
    pfx_certificate_path: str
    pfx_certificate_password: str
    request_timeout: int
    inbound_api_key_name: str
    inbound_api_key: str


def load_settings() -> AppSettings:
    required = {
        "BASE_URL_IA": os.getenv("BASE_URL_IA"),
        "SUBSCRIPTION_KEY": os.getenv("SUBSCRIPTION_KEY"),
        "PFX_CERTIFICATE_PATH": os.getenv("PFX_CERTIFICATE_PATH"),
        "PFX_CERTIFICATE_PASSWORD": os.getenv("PFX_CERTIFICATE_PASSWORD"),
        "INBOUND_API_KEY": os.getenv("INBOUND_API_KEY"),
    }

    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="missing_configuration",
            message=f"Missing required configuration: {', '.join(sorted(missing))}",
        )

    request_timeout_value = os.getenv("REQUEST_TIMEOUT", "30")
    try:
        request_timeout = int(request_timeout_value)
    except ValueError as exc:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="invalid_configuration",
            message="REQUEST_TIMEOUT must be an integer",
        ) from exc

    inbound_api_key_name = os.getenv("INBOUND_API_KEY_NAME", "x-api-key")

    settings = AppSettings(
        base_url_ia=required["BASE_URL_IA"],
        subscription_key=required["SUBSCRIPTION_KEY"],
        pfx_certificate_path=required["PFX_CERTIFICATE_PATH"],
        pfx_certificate_password=required["PFX_CERTIFICATE_PASSWORD"],
        request_timeout=request_timeout,
        inbound_api_key_name=inbound_api_key_name,
        inbound_api_key=required["INBOUND_API_KEY"],
    )

    cert_path = Path(settings.pfx_certificate_path)
    if not cert_path.exists():
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="certificate_not_found",
            message="Configured PFX certificate file was not found",
        )

    if not cert_path.is_file():
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="invalid_certificate_path",
            message="Configured PFX certificate path is not a file",
        )

    return settings


security_header_name = os.getenv("INBOUND_API_KEY_NAME", "x-api-key")
security = APIKeyHeader(name=security_header_name, auto_error=False)

app = FastAPI(
    title="eLicense IA Proxy",
    description="Secure FastAPI proxy for IA API with inbound API key auth and outbound mTLS.",
    version="1.0.0",
)


@app.exception_handler(ProxyError)
async def proxy_error_handler(request: Request, exc: ProxyError) -> JSONResponse:
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


@app.exception_handler(Exception)
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


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
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


async def get_settings_or_raise(request: Request) -> AppSettings:
    startup_error = getattr(request.app.state, "startup_error", None)
    if startup_error:
        raise startup_error

    settings = getattr(request.app.state, "settings", None)
    if not settings:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="missing_configuration",
            message="Application settings are not loaded",
        )
    return settings


async def require_api_key(
    request: Request,
    provided_key: str | None = Depends(security),
    settings: AppSettings = Depends(get_settings_or_raise),
) -> None:
    if provided_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    if provided_key != settings.inbound_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )


def _append_subscription_key(
    url: str,
    subscription_key: str,
    extra_query: list[tuple[str, Any]] | None = None,
) -> str:
    query: list[tuple[str, Any]] = [("subscription-key", subscription_key)]
    if extra_query:
        query.extend([(key, value) for key, value in extra_query if value is not None])
    return f"{url}?{urlencode(query, doseq=True)}"


def _sanitize_backend_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.query:
        return url

    masked_query = urlencode([(key, "***") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, masked_query, parsed.fragment))


def _build_pem_tempfiles(pfx_path: str, pfx_password: str) -> tuple[str, str, list[str]]:
    cert_temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    key_temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    cert_temp_file.close()
    key_temp_file.close()
    cleanup_paths = [cert_temp_file.name, key_temp_file.name]

    try:
        pfx_bytes = Path(pfx_path).read_bytes()
    except FileNotFoundError as exc:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="certificate_not_found",
            message="PFX certificate file does not exist",
        ) from exc

    try:
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            pfx_bytes,
            pfx_password.encode("utf-8"),
        )
    except ValueError as exc:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="invalid_certificate_password",
            message="Unable to decrypt PFX certificate with provided password",
        ) from exc

    if private_key is None or certificate is None:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="invalid_certificate",
            message="PFX certificate is missing private key or certificate",
        )

    cert_chain_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    if additional_certificates:
        for extra_cert in additional_certificates:
            cert_chain_bytes += extra_cert.public_bytes(serialization.Encoding.PEM)

    key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    Path(cert_temp_file.name).write_bytes(cert_chain_bytes)
    Path(key_temp_file.name).write_bytes(key_bytes)

    return cert_temp_file.name, key_temp_file.name, cleanup_paths


def _cleanup_files(paths: list[str]) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove temporary certificate file", extra={"event": "cleanup_warning"})


async def _forward_request(
    request: Request,
    method: str,
    backend_path: str,
    settings: AppSettings,
    body: bytes | None = None,
    extra_query: list[tuple[str, Any]] | None = None,
) -> Response:
    backend_url = _append_subscription_key(
        urljoin(settings.base_url_ia.rstrip("/") + "/", backend_path.lstrip("/")),
        settings.subscription_key,
        extra_query=extra_query,
    )
    safe_backend_url = _sanitize_backend_url(backend_url)

    cert_path, key_path, cleanup_paths = _build_pem_tempfiles(
        settings.pfx_certificate_path,
        settings.pfx_certificate_password,
    )

    started_at = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout, cert=(cert_path, key_path)) as client:
            request_headers = {"content-type": request.headers.get("content-type", "application/json")}
            response = await client.request(
                method=method,
                url=backend_url,
                content=body,
                headers=request_headers,
            )
    except httpx.TimeoutException as exc:
        raise ProxyError(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            code="backend_timeout",
            message="Timeout while calling backend service",
        ) from exc
    except httpx.ConnectError as exc:
        raise ProxyError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="backend_connect_error",
            message="Network or DNS error while connecting to backend service",
        ) from exc
    except httpx.RequestError as exc:
        raise ProxyError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="backend_request_error",
            message="Request error while calling backend service",
        ) from exc
    finally:
        _cleanup_files(cleanup_paths)

    backend_duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    total_duration_ms = round((time.perf_counter() - request.state.started_at) * 1000, 2)

    logger.info(
        "Proxy request completed",
        extra={
            "event": "proxy_request",
            "request_id": request.state.request_id,
            "http_method": request.method,
            "endpoint": request.url.path,
            "backend_endpoint": safe_backend_url,
            "response_status": response.status_code,
            "backend_duration_ms": backend_duration_ms,
            "duration_ms": total_duration_ms,
        },
    )

    passthrough_headers: dict[str, str] = {}
    content_type = response.headers.get("content-type")
    if content_type:
        passthrough_headers["content-type"] = content_type

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=passthrough_headers,
    )


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
    body = await request.body()
    try:
        json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProxyError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_json",
            message="Request body must be valid JSON",
        ) from exc

    return await _forward_request(
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
    body = await request.body()
    try:
        json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProxyError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_json",
            message="Request body must be valid JSON",
        ) from exc

    return await _forward_request(
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
    return await _forward_request(
        request=request,
        method="DELETE",
        backend_path="/ia/documents",
        settings=settings,
        body=body,
        extra_query=query_params,
    )
