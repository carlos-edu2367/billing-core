import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.domain.enums.gateway_provider import GatewayProvider
from app.infra.config import settings
from app.infra.jobs import update_job_metadata
from app.infra.interfaces.gateway_provider import GetGatewayInfra
from app.web.dependencies.common import get_redis_pool
from app.web.dependencies.rate_limit import webhook_rate_limit
from app.web.dependencies.security import validate_asaas_webhook
from app.web.schemas.common import AcceptedJobResponse, build_error_responses


router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post(
    "/asaas",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AcceptedJobResponse,
    dependencies=[Depends(webhook_rate_limit())],
    summary="Receber webhook Asaas",
    description="Valida, normaliza e enfileira eventos recebidos do Asaas.",
    responses=build_error_responses(400, 401, 409, 413, 415, 422, 429, 500),
)
async def receive_asaas_webhook(
    http_request: Request,
    raw_body: bytes = Depends(validate_asaas_webhook),
    redis=Depends(get_redis_pool),
):
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook deve ser um JSON valido.",
        ) from exc

    gateway_provider = GatewayProvider.ASAAS
    gateway = GetGatewayInfra().get(gateway_provider)
    try:
        normalized_payload = gateway.normalize_webhook(payload)
        normalized_payload.event_id_for(gateway_provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    job = await redis.enqueue_job(
        "workers:tasks.process_webhook",
        normalized_payload.model_dump(mode="json"),
        gateway_provider.name,
    )
    await update_job_metadata(
        redis,
        job.job_id,
        status="queued",
        job_name="process_webhook",
        attempt=0,
        max_tries=settings.WORKER_MAX_TRIES,
        request_id=http_request.state.request_id,
        created_at=datetime.now(timezone.utc),
        provider=gateway_provider.value,
        resource_type="webhook",
        source_event_id=normalized_payload.source_event_id or normalized_payload.details.id,
    )

    return {"job_id": job.job_id, "message": "Webhook recebido para processamento."}
