from datetime import datetime, timezone
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.db.setup import get_db
from app.infra.repo.subscription_repo import SubscriptionRepositoryINFRA
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
from app.web.schemas.subscription_cancel import CancelSubscriptionRequest
from app.domain.errors import NotFoundError
from app.domain.enums.subscription_status import SubscriptionStatus


router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])
logger = logging.getLogger(__name__)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    summary="Criar assinatura",
    description="""
Cria uma solicitação de assinatura de forma assíncrona.

### Fluxo
1. O consumidor interno autentica com `X-System` e `X-API-Key`.
2. A API valida o payload e a compatibilidade entre o header `X-System` e o campo `system`.
3. A chave `Idempotency-Key` evita duplicidade de processamento para a mesma intenção de escrita.
4. O Billing Core enfileira o job no worker e responde com `job_id`.
5. O cliente consulta o andamento em `GET /v1/jobs/{job_id}`.

### Headers obrigatórios
- `X-System`: sistema interno chamador.
- `X-API-Key`: credencial do sistema.
- `Idempotency-Key`: chave única por tentativa lógica de criação.

### Regras importantes do payload
- `system` deve corresponder ao sistema autenticado.
- `webhook_link` deve usar HTTPS e host permitido.
- `next_due_date` não pode estar no passado.
- `expires_at` deve estar no futuro.
""",
    responses=build_error_responses(400, 401, 403, 409, 422, 429, 500),
)
async def create_subscription(
    payload: CreateSubscriptionRequest,
    http_request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description=(
            "Chave de idempotência única por intenção de criação. "
            "Se a mesma chave for reutilizada com o mesmo payload, o job original será reaproveitado."
        ),
        examples=["sub-neectify-shop-001"],
    ),
    auth: AuthContext = Depends(require_internal_auth("subscriptions:create")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
):
    logger.info(
        "Subscription creation request received",
        extra={"request_id": http_request.state.request_id, "system": auth.system.value},
    )

    if auth.system != payload.system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema sem permissao para criar essa assinatura.",
        )

    request_hash = build_request_hash(payload.model_dump(mode="json"))
    existing_job = await start_idempotent_job(redis, auth.system, idempotency_key, request_hash)
    if existing_job:
        logger.info(
            "Idempotent subscription creation request reused",
            extra={"request_id": http_request.state.request_id, "system": auth.system.value, "job_id": existing_job["job_id"]},
        )
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
    logger.info(
        "Subscription creation job enqueued",
        extra={"request_id": http_request.state.request_id, "system": auth.system.value, "job_id": job.job_id},
    )

    return {"job_id": job.job_id, "message": "Assinatura enviada para processamento."}


@router.post(
    "/{subscription_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    summary="Cancelar assinatura",
    description="""
Solicita o cancelamento assíncrono de uma assinatura existente.

### Fluxo
1. O consumidor interno autentica com `X-System` e `X-API-Key`.
2. A API valida posse da assinatura, payload e `Idempotency-Key`.
3. O Billing Core enfileira o job de cancelamento.
4. O worker executa o cancelamento no gateway e sincroniza o estado local.
5. O cliente consulta o andamento em `GET /v1/jobs/{job_id}`.

### Headers obrigatórios
- `X-System`: sistema interno chamador.
- `X-API-Key`: credencial do sistema.
- `Idempotency-Key`: chave única por intenção lógica de cancelamento.
""",
    responses=build_error_responses(401, 403, 404, 409, 422, 429, 500),
)
async def cancel_subscription(
    subscription_id: UUID,
    payload: CancelSubscriptionRequest,
    http_request: Request,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="Chave única por intenção lógica de cancelamento da assinatura.",
        examples=["cancel-subscription-001"],
    ),
    auth: AuthContext = Depends(require_internal_auth("subscriptions:cancel")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
    db: AsyncSession = Depends(get_db),
):
    logger.info(
        "Subscription cancellation request received",
        extra={
            "request_id": http_request.state.request_id,
            "system": auth.system.value,
            "subscription_id": str(subscription_id),
        },
    )

    repo = SubscriptionRepositoryINFRA(db)
    try:
        subscription = await repo.get_by_id(subscription_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assinatura nao encontrada.") from exc

    if not subscription.belongs_to_system(auth.system):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assinatura nao encontrada.")

    if subscription.status == SubscriptionStatus.CANCELED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Assinatura ja cancelada.")

    request_hash = build_request_hash(
        {
            "subscription_id": str(subscription_id),
            **payload.model_dump(mode="json"),
        }
    )
    namespace = f"subscription_cancel:{subscription_id}"
    existing_job = await start_idempotent_job(
        redis,
        auth.system,
        idempotency_key,
        request_hash,
        namespace=namespace,
    )
    if existing_job:
        logger.info(
            "Idempotent subscription cancellation request reused",
            extra={
                "request_id": http_request.state.request_id,
                "system": auth.system.value,
                "subscription_id": str(subscription_id),
                "job_id": existing_job["job_id"],
            },
        )
        return {
            "job_id": existing_job["job_id"],
            "message": "Cancelamento ja recebido anteriormente. Retornando job existente.",
        }

    try:
        job = await redis.enqueue_job(
            "workers:tasks.cancel_subscription_worker",
            payload.to_worker_payload(subscription_id, auth.system.value, ""),
        )
    except Exception:
        await clear_idempotent_job(redis, auth.system, idempotency_key, namespace=namespace)
        raise

    await save_idempotent_job(
        redis,
        auth.system,
        idempotency_key,
        request_hash,
        job.job_id,
        namespace=namespace,
    )
    await redis.setex(
        f"billing_core:job_owner:{job.job_id}",
        settings.JOB_METADATA_TTL_SECONDS,
        auth.system.value,
    )
    await update_job_metadata(
        redis,
        job.job_id,
        status="queued",
        job_name="cancel_subscription_worker",
        attempt=0,
        max_tries=settings.WORKER_MAX_TRIES,
        request_id=http_request.state.request_id,
        created_at=datetime.now(timezone.utc),
        system=auth.system.value,
        resource_type="subscription",
        resource_id=str(subscription_id),
        operation="cancel",
    )
    logger.info(
        "Subscription cancellation job enqueued",
        extra={
            "request_id": http_request.state.request_id,
            "system": auth.system.value,
            "subscription_id": str(subscription_id),
            "job_id": job.job_id,
        },
    )
    response.headers["X-Job-ID"] = job.job_id
    return {"job_id": job.job_id, "message": "Cancelamento enviado para processamento."}
