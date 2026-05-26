from datetime import datetime, timezone
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dtos.response.payment import PaymentStatusResponse
from app.domain.errors import NotFoundError
from app.infra.config import settings
from app.infra.db.setup import get_db
from app.infra.jobs import update_job_metadata
from app.infra.repo.payment_repo import PaymentRepositoryINFRA
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
from app.web.schemas.payment import CreatePaymentRequest


router = APIRouter(prefix="/v1/payments", tags=["payments"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    summary="Criar pagamento avulso",
    responses=build_error_responses(400, 401, 403, 409, 422, 429, 500),
)
async def create_payment(
    payload: CreatePaymentRequest,
    http_request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    auth: AuthContext = Depends(require_internal_auth("payments:create")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
):
    if auth.system != payload.system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema sem permissao para criar esse pagamento.",
        )

    request_hash = build_request_hash(payload.model_dump(mode="json"))
    namespace = "payment_create"
    existing_job = await start_idempotent_job(redis, auth.system, idempotency_key, request_hash, namespace=namespace)
    if existing_job:
        logger.info(
            "Idempotent payment creation request reused",
            extra={"request_id": http_request.state.request_id, "system": auth.system.value, "job_id": existing_job["job_id"]},
        )
        return {
            "job_id": existing_job["job_id"],
            "message": "Pagamento ja recebido anteriormente. Retornando job existente.",
        }

    try:
        job = await redis.enqueue_job(
            "workers:tasks.create_payment_worker",
            payload.to_worker_payload(),
            payload.customer_provider_id,
            payload.system.name,
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
        job_name="create_payment_worker",
        attempt=0,
        max_tries=settings.WORKER_MAX_TRIES,
        request_id=http_request.state.request_id,
        created_at=datetime.now(timezone.utc),
        system=auth.system.value,
        resource_type="payment",
    )
    logger.info(
        "Payment creation job enqueued",
        extra={"request_id": http_request.state.request_id, "system": auth.system.value, "job_id": job.job_id},
    )

    return {"job_id": job.job_id, "message": "Pagamento enviado para processamento."}


@router.get(
    "/{payment_id}",
    response_model=PaymentStatusResponse,
    summary="Consultar pagamento local",
    responses=build_error_responses(401, 403, 404, 422, 429, 500),
)
async def get_payment_status(
    payment_id: UUID,
    response: Response,
    auth: AuthContext = Depends(require_internal_auth("payments:read")),
    redis=Depends(get_redis_pool),
    db: AsyncSession = Depends(get_db),
):
    polling_key = f"billing_core:payment_poll:{auth.system.value}:{payment_id}"
    allowed = await redis.set(polling_key, "1", ex=10, nx=True)
    if not allowed:
        response.headers["Retry-After"] = "10"
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Consulte esse pagamento em intervalos minimos de 10 segundos.",
            headers={"Retry-After": "10"},
        )

    repo = PaymentRepositoryINFRA(db)
    try:
        payment = await repo.get_by_id(payment_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pagamento nao encontrado.") from exc

    if payment.from_system != auth.system:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pagamento nao encontrado.")

    return PaymentStatusResponse(
        payment_id=payment.id,
        system_payment_id=payment.system_payment_id,
        value=payment.value,
        checkout_url=payment.checkout_link,
        payment_status=payment.payment_status,
        billing_type=payment.payment_type,
        due_date=payment.due_date,
        paid_date=payment.paid_date,
        updated_at=payment.updated_at,
    )
