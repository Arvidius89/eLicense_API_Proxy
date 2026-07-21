import os

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from .settings import AppSettings, get_settings_or_raise


security = APIKeyHeader(name=os.getenv("INBOUND_API_KEY_NAME", "x-api-key"), auto_error=False)


async def require_api_key(
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
