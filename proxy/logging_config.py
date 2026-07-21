import json
import logging
from datetime import datetime, timezone
from pathlib import Path


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

    # Prevent dependency loggers from emitting backend URLs to stdout/Application Insights.
    for noisy_logger_name in (
        "httpx",
        "httpx._client",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.proxy",
    ):
        noisy_logger = logging.getLogger(noisy_logger_name)
        noisy_logger.handlers.clear()
        noisy_logger.setLevel(logging.WARNING)
        noisy_logger.propagate = False

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(JsonFormatter())
    logger.addHandler(stream_handler)

    try:
        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(logs_dir / "proxy.log")
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
    except OSError:
        # File logging is a local development convenience only. Some hosting
        # environments (e.g. Azure Functions Flex Consumption during worker
        # indexing) may have a read-only or unexpected working directory, so
        # failing to create the log file must never prevent the app/module
        # from importing successfully. Stdout logging above still works.
        logger.warning(
            "File logging unavailable; continuing with stdout logging only",
            extra={"event": "file_logging_unavailable"},
        )

    logger.propagate = False
    return logger


logger = configure_logging()
