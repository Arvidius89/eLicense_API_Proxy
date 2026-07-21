import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Request, status

from .errors import ProxyError


load_dotenv()


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

    settings = AppSettings(
        base_url_ia=required["BASE_URL_IA"],
        subscription_key=required["SUBSCRIPTION_KEY"],
        pfx_certificate_path=required["PFX_CERTIFICATE_PATH"],
        pfx_certificate_password=required["PFX_CERTIFICATE_PASSWORD"],
        request_timeout=request_timeout,
        inbound_api_key_name=os.getenv("INBOUND_API_KEY_NAME", "x-api-key"),
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


async def get_settings_or_raise(request: Request) -> AppSettings:
    startup_error = getattr(request.app.state, "startup_error", None)
    if startup_error:
        raise startup_error

    settings = getattr(request.app.state, "settings", None)
    if not settings:
        settings = load_settings()
        request.app.state.settings = settings
        request.app.state.startup_error = None
    return settings
