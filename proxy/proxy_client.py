import time
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from fastapi import Request, status
from fastapi.responses import Response

from .certificates import build_pem_tempfiles, cleanup_files
from .errors import ProxyError
from .logging_config import logger
from .settings import AppSettings


def append_subscription_key(
    url: str,
    subscription_key: str,
    extra_query: list[tuple[str, Any]] | None = None,
) -> str:
    query: list[tuple[str, Any]] = [("subscription-key", subscription_key)]
    if extra_query:
        query.extend([(key, value) for key, value in extra_query if value is not None])
    return f"{url}?{urlencode(query, doseq=True)}"


def sanitize_backend_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.query:
        return url

    masked_query = "&".join(
        f"{quote_plus(key)}=***" for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, masked_query, parsed.fragment))


async def forward_request(
    request: Request,
    method: str,
    backend_path: str,
    settings: AppSettings,
    body: bytes | None = None,
    extra_query: list[tuple[str, Any]] | None = None,
) -> Response:
    backend_url = append_subscription_key(
        urljoin(settings.base_url_ia.rstrip("/") + "/", backend_path.lstrip("/")),
        settings.subscription_key,
        extra_query=extra_query,
    )
    safe_backend_url = sanitize_backend_url(backend_url)

    cert_path, key_path, cleanup_paths = build_pem_tempfiles(
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
        cleanup_files(cleanup_paths)

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
