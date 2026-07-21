import base64
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from fastapi import status

from .errors import ProxyError
from .logging_config import logger


def _read_pfx_bytes(pfx_path: str | None, pfx_base64: str | None) -> bytes:
    if pfx_base64:
        try:
            return base64.b64decode(pfx_base64, validate=True)
        except ValueError as exc:
            raise ProxyError(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="invalid_certificate_data",
                message="PFX_CERTIFICATE_BASE64 is not valid base64 data",
            ) from exc

    if not pfx_path:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="missing_configuration",
            message="No PFX certificate source configured",
        )

    try:
        return Path(pfx_path).read_bytes()
    except FileNotFoundError as exc:
        raise ProxyError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="certificate_not_found",
            message="PFX certificate file does not exist",
        ) from exc


def build_pem_tempfiles(
    pfx_path: str | None,
    pfx_password: str,
    pfx_base64: str | None = None,
) -> tuple[str, str, list[str]]:
    cert_temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    key_temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    cert_temp_file.close()
    key_temp_file.close()
    cleanup_paths = [cert_temp_file.name, key_temp_file.name]

    pfx_bytes = _read_pfx_bytes(pfx_path, pfx_base64)

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


def cleanup_files(paths: list[str]) -> None:
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to remove temporary certificate file",
                extra={"event": "cleanup_warning"},
            )
