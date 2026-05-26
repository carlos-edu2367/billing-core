import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.domain.enums.gateway_provider import GatewayProvider
from app.infra.config import settings
from app.infra.jobs import update_job_metadata
from app.infra.interfaces.gateway_provider import GetGatewayInfra
from app.web.dependencies.common import get_redis_pool
from app.web.dependencies.rate_limit import webhook_rate_limit
from app.web.dependencies.security import WebhookValidationResult, validate_asaas_webhook
from app.web.schemas.common import build_error_responses


router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post(
    "/asaas",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(webhook_rate_limit())],
    summary="Receber webhook Asaas",
    description="""
Recebe um evento bruto do Asaas, valida o secret técnico, protege contra replay e enfileira o processamento.

### Fluxo
1. O Asaas envia o evento para este endpoint.
2. O Billing Core valida o header `asaas-access-token`.
3. O payload precisa ser JSON e respeitar o limite máximo de tamanho.
4. A API aplica proteção contra replay com hash do corpo do request.
5. O evento é normalizado e enviado ao worker.

### Header obrigatório
- `asaas-access-token`: secret configurado em `ASAAS_WEBHOOK_SECRET`.

### Observações
- Este endpoint é técnico e voltado ao gateway.
- O corpo aceito segue o contrato do Asaas e é normalizado internamente antes do processamento.
""",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "description": "Payload bruto enviado pelo Asaas.",
                    },
                    "examples": {
                        "payment_received": {
                            "summary": "Exemplo de evento do Asaas",
                            "value": {
                                "event": "PAYMENT_RECEIVED",
                                "payment": {
                                    "id": "pay_123",
                                    "customer": "cus_123",
                                    "subscription": "sub_123",
                                    "value": 129.90,
                                    "netValue": 127.35,
                                    "billingType": "BOLETO",
                                    "status": "RECEIVED",
                                    "dueDate": "2026-05-01",
                                }
                            },
                        }
                    },
                }
            },
        }
    },
    responses=build_error_responses(400, 401, 413, 415, 422, 429, 500),
)
async def receive_asaas_webhook(
    http_request: Request,
    webhook_validation: WebhookValidationResult = Depends(validate_asaas_webhook),
    redis=Depends(get_redis_pool),
):
    if webhook_validation.duplicate:
        return {"received": True, "duplicate": True}

    try:
        payload = json.loads(webhook_validation.raw_body)
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
    await redis.setex(
        webhook_validation.replay_key,
        settings.WEBHOOK_REPLAY_TTL_SECONDS,
        "1",
    )

    return {"job_id": job.job_id, "message": "Webhook recebido para processamento."}
