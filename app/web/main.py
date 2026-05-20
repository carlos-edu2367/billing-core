from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4
import logging

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.infra.config import settings
from app.infra.observability import configure_logging, correlation_id_var, metrics

from app.web.errors import register_exception_handlers, register_request_too_large
from app.web.routes.customers import router as customers_router
from app.web.routes.health import router as health_router
from app.web.routes.jobs import router as jobs_router
from app.web.routes.subscriptions import router as subscriptions_router
from app.web.routes.webhooks import router as webhooks_router


configure_logging(settings.LOG_LEVEL)
settings.validate_runtime()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app_: FastAPI):
    redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    app_.state.redis_pool = redis_pool
    logger.info(
        "Application started",
        extra={"service_name": settings.SERVICE_NAME, "environment": settings.APP_ENV},
    )
    yield
    logger.info("Application stopping", extra={"service_name": settings.SERVICE_NAME})
    await redis_pool.aclose()

app = FastAPI(
    title="Billing Core API",
    version="1.0.0",
    description="API do Billing Core para orquestracao de assinaturas e cobrancas da Neectify.",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_API_DOCS else None,
    openapi_tags=[
        {"name": "health", "description": "Endpoints operacionais da API."},
        {"name": "customers", "description": "Cadastro de clientes no provedor de pagamento."},
        {"name": "subscriptions", "description": "Criacao e processamento de assinaturas."},
        {"name": "jobs", "description": "Consulta de processamento assincrono."},
        {"name": "webhooks", "description": "Recepcao de eventos de gateways externos."},
    ],
)

if settings.CORS_ALLOW_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOW_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-API-Key", "X-System", "asaas-access-token"],
    )


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
                response = register_request_too_large(request_id)
                return response
        except ValueError:
            logger.warning(
                "Invalid content-length header",
                extra={"request_id": request_id, "path": request.url.path, "content_length": content_length},
            )

    start = perf_counter()
    token = correlation_id_var.set(request_id)
    request.state.request_id = request_id

    try:
        response = await call_next(request)
    except Exception:
        correlation_id_var.reset(token)
        raise

    correlation_id_var.reset(token)

    duration_ms = round((perf_counter() - start) * 1000, 2)
    metrics.increment(
        "http_requests_total",
        method=request.method,
        path=request.url.path,
        status_code=str(response.status_code),
    )
    metrics.observe(
        "http_request_duration_ms",
        duration_ms,
        method=request.method,
        path=request.url.path,
    )
    logger.info(
        "HTTP request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Request-ID"] = request_id
    return response


register_exception_handlers(app)

app.include_router(health_router)
app.include_router(customers_router)
app.include_router(webhooks_router)
app.include_router(subscriptions_router)
app.include_router(jobs_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
