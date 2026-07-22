import json
import time
import uuid
from copy import deepcopy
from typing import Any
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field, RootModel

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


class HealthResponse(BaseModel):
    status: Literal["healthy"]


class DocumentRef(BaseModel):
    docType: str = Field(..., examples=["org.iso.23220.1.nl.kiwa.sampcert"])
    document_number: str = Field(..., examples=["Kiwa_260303_1"])


class ActivationStatusRequest(RootModel[list[DocumentRef]]):
    pass


class CreateSavedDocument(BaseModel):
    document_number: str
    activation_code: str | None = None
    oid4vci_credential_offer: str | None = None
    model_config = ConfigDict(extra="allow")


class FailedDocument(BaseModel):
    document_number: str
    error: str | None = None
    model_config = ConfigDict(extra="allow")


class CreateDocumentsResponse(BaseModel):
    request_id: str | None = None
    documents_saved: int
    documents_failed: int
    saved_document_numbers: list[CreateSavedDocument]
    failed_document_numbers: list[FailedDocument]
    created: str | None = None
    model_config = ConfigDict(extra="allow")


class ActivationStatusDocumentStatus(BaseModel):
    docType: str
    document_number: str
    status: str
    model_config = ConfigDict(extra="allow")


class ActivationStatusResponse(BaseModel):
    documents: list[ActivationStatusDocumentStatus]
    model_config = ConfigDict(extra="allow")


class DeleteDocumentsResponse(BaseModel):
    documents_deleted: int
    documents_failed: int
    deleted_document_numbers: list[str]
    failed_document_numbers: list[FailedDocument]
    model_config = ConfigDict(extra="allow")


class DeleteDocumentsRequest(RootModel[list[DocumentRef]]):
    pass

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


def _convert_schema_to_openapi_30(schema: object) -> object:
    if isinstance(schema, dict):
        converted = {}
        for key, value in schema.items():
            # jsonSchemaDialect is OpenAPI 3.1 specific.
            if key == "$schema":
                continue
            if key == "const":
                converted["enum"] = [value]
                continue
            if key == "examples" and isinstance(value, list):
                if value:
                    converted["example"] = _convert_schema_to_openapi_30(value[0])
                continue
            converted[key] = _convert_schema_to_openapi_30(value)

        any_of = converted.get("anyOf")
        if isinstance(any_of, list):
            non_null = [item for item in any_of if not (isinstance(item, dict) and item.get("type") == "null")]
            if len(non_null) == len(any_of) - 1 and len(non_null) == 1:
                merged = non_null[0]
                if isinstance(merged, dict):
                    merged = deepcopy(merged)
                    merged["nullable"] = True
                    return merged

        return converted

    if isinstance(schema, list):
        return [_convert_schema_to_openapi_30(item) for item in schema]

    return schema


def _remove_validation_error_artifacts(schema: dict[str, Any]) -> None:
    paths = schema.get("paths")
    if isinstance(paths, dict):
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses")
                if isinstance(responses, dict):
                    responses.pop("422", None)

    components = schema.get("components")
    if isinstance(components, dict):
        component_schemas = components.get("schemas")
        if isinstance(component_schemas, dict):
            component_schemas.pop("ValidationError", None)
            component_schemas.pop("HTTPValidationError", None)


def _cleanup_openapi_keywords(node: Any) -> Any:
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            cleaned_value = _cleanup_openapi_keywords(value)
            if cleaned_value is None and key in {"$ref", "anyOf", "oneOf", "allOf"}:
                continue
            cleaned[key] = cleaned_value

        for composition_key in ("anyOf", "oneOf", "allOf"):
            if composition_key in cleaned:
                options = cleaned.get(composition_key)
                if isinstance(options, list) and options:
                    first_option = options[0]
                    if isinstance(first_option, dict):
                        merged_option = _cleanup_openapi_keywords(first_option)
                        if isinstance(merged_option, dict):
                            for option_key, option_value in merged_option.items():
                                cleaned.setdefault(option_key, option_value)
                cleaned.pop(composition_key, None)

        ref_value = cleaned.get("$ref")
        if isinstance(ref_value, str) and (
            ref_value.endswith("/ValidationError") or ref_value.endswith("/HTTPValidationError")
        ):
            return None

        return cleaned

    if isinstance(node, list):
        cleaned_items = []
        for item in node:
            cleaned_item = _cleanup_openapi_keywords(item)
            if cleaned_item is not None:
                cleaned_items.append(cleaned_item)
        return cleaned_items

    return node


async def _read_json_request_body(request: Request) -> bytes:
    body = await request.body()
    try:
        json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProxyError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_json",
            message="Request body must be valid JSON",
        ) from exc
    return body


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Force OpenAPI 3.0.1 for Power Platform Custom Connector compatibility.
    openapi_schema["openapi"] = "3.0.1"
    openapi_schema.pop("jsonSchemaDialect", None)

    converted = _convert_schema_to_openapi_30(openapi_schema)
    if not isinstance(converted, dict):
        raise RuntimeError("Generated OpenAPI schema must be an object")

    _remove_validation_error_artifacts(converted)
    converted = _cleanup_openapi_keywords(converted)
    if not isinstance(converted, dict):
        raise RuntimeError("Processed OpenAPI schema must be an object")

    app.openapi_schema = converted
    return converted


app.openapi = custom_openapi


@app.get("/health", tags=["System"], response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="healthy")


@app.post(
    "/documents",
    dependencies=[Depends(require_api_key)],
    tags=["Documents"],
    response_model=CreateDocumentsResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "example": {
                        "documents": [
                            {
                                "docType": "org.iso.23220.1.eu.europe.emsa.coc",
                                "namespaces": {
                                    "org.iso.23220.1.eu.europe.emsa.coc": {
                                        "document_number": "Koopvaardij_38"
                                    }
                                },
                            }
                        ],
                        "license_holder": {
                            "last_name": "test",
                            "first_name": "tester",
                            "email": "test@tester.nl",
                        },
                    },
                }
            },
        }
    },
)
async def create_documents(
    request: Request,
    settings: AppSettings = Depends(get_settings_or_raise),
) -> Response:
    body = await _read_json_request_body(request)
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
    response_model=ActivationStatusResponse,
)
async def activation_status(
    payload: ActivationStatusRequest,
    request: Request,
    settings: AppSettings = Depends(get_settings_or_raise),
) -> Response:
    body = payload.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8")
    return await forward_request(
        request=request,
        method="POST",
        backend_path="/ia/documents/activation-status",
        settings=settings,
        body=body,
    )


@app.post(
    "/documents/delete",
    dependencies=[Depends(require_api_key)],
    tags=["Documents"],
    response_model=DeleteDocumentsResponse,
)
async def delete_documents(
    request: Request,
    payload: DeleteDocumentsRequest,
    settings: AppSettings = Depends(get_settings_or_raise),
) -> Response:
    request_id = getattr(request.state, "request_id", None)
    logger.info(
        "POST /documents/delete ontvangen",
        extra={
            "event": "delete_documents_received",
            "request_id": request_id,
            "endpoint": request.url.path,
            "http_method": request.method,
        },
    )

    document_count = len(payload.root)
    logger.info(
        "aantal documenten: %s",
        document_count,
        extra={
            "event": "delete_documents_count",
            "request_id": request_id,
            "endpoint": request.url.path,
            "http_method": request.method,
        },
    )

    body = await request.body()
    logger.info(
        "request doorgestuurd naar backend",
        extra={
            "event": "delete_documents_forwarded",
            "request_id": request_id,
            "endpoint": request.url.path,
            "http_method": request.method,
        },
    )

    try:
        response = await forward_request(
            request=request,
            method="DELETE",
            backend_path="/ia/documents",
            settings=settings,
            body=body,
            log_request=False,
        )
    except ProxyError as exc:
        logger.error(
            "POST /documents/delete fout",
            extra={
                "event": "delete_documents_error",
                "request_id": request_id,
                "endpoint": request.url.path,
                "http_method": request.method,
                "response_status": exc.status_code,
            },
        )
        raise

    logger.info(
        "backend status: %s",
        response.status_code,
        extra={
            "event": "delete_documents_backend_status",
            "request_id": request_id,
            "endpoint": request.url.path,
            "http_method": request.method,
            "response_status": response.status_code,
        },
    )
    logger.info(
        "POST /documents/delete succesvol",
        extra={
            "event": "delete_documents_success",
            "request_id": request_id,
            "endpoint": request.url.path,
            "http_method": request.method,
            "response_status": response.status_code,
        },
    )
    return response
