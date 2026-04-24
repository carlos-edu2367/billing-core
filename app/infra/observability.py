import contextvars
import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Any


correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        correlation_id = correlation_id_var.get()
        if correlation_id:
            payload["correlation_id"] = correlation_id

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "args",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                continue
            if key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(level: str) -> None:
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)
    root_logger.setLevel(level.upper())


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: Counter[str] = Counter()
        self._durations_ms: defaultdict[str, list[float]] = defaultdict(list)
        self._started_at = datetime.now(timezone.utc)

    def increment(self, name: str, value: int = 1, **labels: str) -> None:
        key = self._build_key(name, labels)
        with self._lock:
            self._counters[key] += value

    def observe(self, name: str, duration_ms: float, **labels: str) -> None:
        key = self._build_key(name, labels)
        with self._lock:
            self._durations_ms[key].append(duration_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            durations = {
                key: {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values), 2) if values else 0.0,
                    "max_ms": round(max(values), 2) if values else 0.0,
                }
                for key, values in self._durations_ms.items()
            }

        return {
            "started_at": self._started_at.isoformat(),
            "uptime_seconds": int(time.time() - self._started_at.timestamp()),
            "counters": counters,
            "durations_ms": durations,
        }

    @staticmethod
    def _build_key(name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
        return f"{name}|{label_str}"


metrics = MetricsRegistry()
