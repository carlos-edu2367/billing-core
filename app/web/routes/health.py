from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.infra.config import settings
from app.infra.db.setup import AsyncSessionLocal
from app.infra.observability import metrics
from app.web.dependencies.common import get_redis_pool
from app.web.dependencies.rate_limit import internal_rate_limit
from app.web.dependencies.security import AuthContext, require_internal_auth
from app.web.schemas.common import build_error_responses
from app.web.schemas.health import HealthResponse, LivenessResponse, ReadinessResponse
from app.web.schemas.observability import DependencyStatus, MetricsResponse, ReadinessDetailResponse


router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Verifica se a API esta respondendo.",
)
async def health():
    return {"status": "ok"}


@router.get(
    "/ready",
    response_model=ReadinessDetailResponse,
    summary="Readiness check",
    description="Verifica se a API esta pronta para receber trafego.",
)
async def readiness(redis=Depends(get_redis_pool)):
    dependencies: dict[str, DependencyStatus] = {}

    redis_start = perf_counter()
    try:
        await redis.ping()
        dependencies["redis"] = DependencyStatus(
            status="up",
            latency_ms=round((perf_counter() - redis_start) * 1000, 2),
        )
    except Exception as exc:
        dependencies["redis"] = DependencyStatus(
            status="down",
            latency_ms=round((perf_counter() - redis_start) * 1000, 2),
            detail=str(exc),
        )

    db_start = perf_counter()
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        dependencies["database"] = DependencyStatus(
            status="up",
            latency_ms=round((perf_counter() - db_start) * 1000, 2),
        )
    except Exception as exc:
        dependencies["database"] = DependencyStatus(
            status="down",
            latency_ms=round((perf_counter() - db_start) * 1000, 2),
            detail=str(exc),
        )

    overall_status = "ready" if all(item.status == "up" for item in dependencies.values()) else "degraded"
    metrics.increment("readiness_checks_total", status=overall_status)
    return ReadinessDetailResponse(status=overall_status, dependencies=dependencies)


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness check",
    description="Verifica se o processo da API esta vivo.",
)
async def liveness():
    return {"status": "alive"}


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Metrics",
    description="Retorna metricas basicas de operacao da API e dos workers.",
    responses=build_error_responses(401, 403, 404, 413, 429, 500),
)
async def metrics_endpoint(
    request: Request,
    _auth: AuthContext = Depends(require_internal_auth("metrics:read")),
    _rate_limiter=Depends(internal_rate_limit()),
):
    if not settings.ENABLE_METRICS_ENDPOINT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint nao habilitado.")

    app_metrics = metrics.snapshot()
    app_metrics["service_name"] = request.app.title
    return app_metrics
