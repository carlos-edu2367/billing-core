from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.infra.config import settings
from app.infra.jobs import update_job_metadata
from app.web.dependencies.common import get_redis_pool
from app.web.dependencies.idempotency import (
    build_request_hash,
    clear_idempotent_job,
    save_idempotent_job,
    start_idempotent_job,
)
from app.web.dependencies.rate_limit import internal_rate_limit
from app.web.dependencies.security import AuthContext, require_internal_auth
from app.web.schemas.common import AcceptedJobResponse, build_error_responses
from app.web.schemas.subscription import CreateSubscriptionRequest


router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    summary="Criar assinatura",
    description="Recebe uma solicitacao de assinatura e coloca o processamento em fila.",
    responses=build_error_responses(400, 401, 403, 409, 422, 429, 500),
)
async def create_subscription(
    payload: CreateSubscriptionRequest,
    http_request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="Chave de idempotencia unica por tentativa de criacao.",
    ),
    auth: AuthContext = Depends(require_internal_auth("subscriptions:create")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
):
    if auth.system != payload.system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema sem permissao para criar essa assinatura.",
        )

    request_hash = build_request_hash(payload.model_dump(mode="json"))
    existing_job = await start_idempotent_job(redis, auth.system, idempotency_key, request_hash)
    if existing_job:
        return {
            "job_id": existing_job["job_id"],
            "message": "Assinatura ja recebida anteriormente. Retornando job existente.",
        }

    try:
        job = await redis.enqueue_job(
            "workers:tasks.create_subscription_worker",
            payload.to_worker_payload(),
            payload.customer_provider_id,
            payload.system.name,
        )
    except Exception:
        await clear_idempotent_job(redis, auth.system, idempotency_key)
        raise

    await save_idempotent_job(redis, auth.system, idempotency_key, request_hash, job.job_id)
    await redis.setex(
        f"billing_core:job_owner:{job.job_id}",
        settings.JOB_METADATA_TTL_SECONDS,
        auth.system.value,
    )
    await update_job_metadata(
        redis,
        job.job_id,
        status="queued",
        job_name="create_subscription_worker",
        attempt=0,
        max_tries=settings.WORKER_MAX_TRIES,
        request_id=http_request.state.request_id,
        created_at=datetime.now(timezone.utc),
        system=auth.system.value,
        resource_type="subscription",
    )

    return {"job_id": job.job_id, "message": "Assinatura enviada para processamento."}
