from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.domain.enums.gateway_provider import GatewayProvider
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
from app.web.schemas.payment_link import CreatePaymentLinkRequest


router = APIRouter(prefix="/v1/payment-links", tags=["payment-links"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    summary="Criar link de pagamento avulso",
    responses=build_error_responses(400, 401, 403, 409, 422, 429, 500),
)
async def create_payment_link(
    payload: CreatePaymentLinkRequest,
    http_request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_internal_auth("payments:create")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
):
    if auth.system != payload.system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema sem permissao para criar esse link de pagamento.",
        )

    request_hash = build_request_hash(payload.model_dump(mode="json"))
    namespace = "payment_link_create"
    existing_job = await start_idempotent_job(redis, auth.system, idempotency_key, request_hash, namespace=namespace)
    if existing_job:
        logger.info(
            "Idempotent payment link creation request reused",
            extra={"request_id": http_request.state.request_id, "system": auth.system.value, "job_id": existing_job["job_id"]},
        )
        return {
            "job_id": existing_job["job_id"],
            "message": "Checkout ja recebido anteriormente. Retornando job existente.",
        }

    try:
        job = await redis.enqueue_job(
            "workers:tasks.create_payment_link_worker",
            payload.to_worker_payload(),
            GatewayProvider.ASAAS.name,
        )
    except Exception:
        await clear_idempotent_job(redis, auth.system, idempotency_key, namespace=namespace)
        raise

    await save_idempotent_job(redis, auth.system, idempotency_key, request_hash, job.job_id, namespace=namespace)
    await redis.setex(
        f"billing_core:job_owner:{job.job_id}",
        settings.JOB_METADATA_TTL_SECONDS,
        auth.system.value,
    )
    await update_job_metadata(
        redis,
        job.job_id,
        status="queued",
        job_name="create_payment_link_worker",
        attempt=0,
        max_tries=settings.WORKER_MAX_TRIES,
        request_id=http_request.state.request_id,
        created_at=datetime.now(timezone.utc),
        system=auth.system.value,
        resource_type="payment_link",
    )
    logger.info(
        "Payment link creation job enqueued",
        extra={"request_id": http_request.state.request_id, "system": auth.system.value, "job_id": job.job_id},
    )

    return {"job_id": job.job_id, "message": "Checkout enviado para criacao."}
