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
