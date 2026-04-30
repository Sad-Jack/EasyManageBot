from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "event": getattr(record, "event", record.funcName),
            "message": record.getMessage(),
            "context": getattr(record, "context", {}),
        }
        if record.exc_info:
            payload["stacktrace"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ConsoleLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).astimezone().strftime("%H:%M:%S")
        component = getattr(record, "component", record.name)
        event = getattr(record, "event", "")
        base = f"{ts} {record.levelname:<5} {component:<18} {record.getMessage()}"
        if event:
            base = f"{base} event={event}"
        context = getattr(record, "context", None)
        if context:
            ctx_pairs = " ".join(f"{key}={value}" for key, value in context.items())
            base = f"{base} {ctx_pairs}"
        if record.exc_info and record.levelno <= logging.DEBUG:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def setup_logging(*, level: str, log_format: str, log_to_file: bool, log_file_path: str) -> None:
    root = logging.getLogger()
    root.setLevel(_normalize_level(level))
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ConsoleLogFormatter())
    root.addHandler(console_handler)

    file_formatter: logging.Formatter = JsonLogFormatter() if log_format.lower() == "json" else ConsoleLogFormatter()
    if log_to_file:
        path = Path(log_file_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(file_formatter)
        root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)


def log_event(
    logger: logging.Logger,
    *,
    level: int,
    component: str,
    event: str,
    message: str,
    context: dict[str, Any] | None = None,
    exc_info: BaseException | tuple[type[BaseException], BaseException, Any] | None = None,
) -> None:
    logger.log(
        level,
        message,
        extra={
            "component": component,
            "event": event,
            "context": context or {},
        },
        exc_info=exc_info,
    )


def _normalize_level(level: str) -> int:
    mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return mapping.get(level.upper(), logging.INFO)
