"""Structured logging to logs/ with required fields."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from core import paths

_CONFIGURED = False

# log file per pipeline concern (spec §51)
_LOG_FILES = {
    "manager": "manager.log",
    "discovery": "discovery.log",
    "download": "download.log",
    "build": "build.log",
    "errors": "errors.log",
}


class StructuredFormatter(logging.Formatter):
    """One JSON object per line with timestamp/stage/message and context."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(record.created)
            ),
            "level": record.levelname,
            "stage": getattr(record, "stage", record.name),
            "message": record.getMessage(),
        }
        for key in ("ustaz_id", "video_id", "job_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _ensure_dirs() -> None:
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    _ensure_dirs()
    root = logging.getLogger("archive")
    if _CONFIGURED:
        return root

    root.setLevel(level)
    formatter = StructuredFormatter()

    manager_handler = logging.FileHandler(
        paths.LOGS_DIR / _LOG_FILES["manager"], encoding="utf-8"
    )
    manager_handler.setFormatter(formatter)
    manager_handler.setLevel(level)
    root.addHandler(manager_handler)

    errors_handler = logging.FileHandler(
        paths.LOGS_DIR / _LOG_FILES["errors"], encoding="utf-8"
    )
    errors_handler.setFormatter(formatter)
    errors_handler.setLevel(logging.WARNING)
    root.addHandler(errors_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    console.setLevel(level)
    root.addHandler(console)

    _CONFIGURED = True
    return root


def get_logger(stage: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"archive.{stage}")


def log_event(
    stage: str,
    message: str,
    *,
    level: int = logging.INFO,
    ustaz_id: str | None = None,
    video_id: str | None = None,
    job_id: int | None = None,
) -> None:
    logger = get_logger(stage)
    logger.log(
        level,
        message,
        extra={
            "stage": stage,
            "ustaz_id": ustaz_id,
            "video_id": video_id,
            "job_id": job_id,
        },
    )
