from typing import Any, Literal

from pydantic import BaseModel


class DependencyStatus(BaseModel):
    status: Literal["up", "down"]
    latency_ms: float | None = None
    detail: str | None = None


class ReadinessDetailResponse(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: dict[str, DependencyStatus]


class MetricsResponse(BaseModel):
    service_name: str
    started_at: str
    uptime_seconds: int
    counters: dict[str, int]
    durations_ms: dict[str, dict[str, Any]]
