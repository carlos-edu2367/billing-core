import hashlib
import json

from fastapi import HTTPException, status

from app.domain.enums.system import System
from app.infra.config import settings


def build_request_hash(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _build_idempotency_key(namespace: str, system: System, idempotency_key: str) -> str:
    normalized_namespace = (namespace or "subscription").strip() or "subscription"
    return f"billing_core:idempotency:{normalized_namespace}:{system.value}:{idempotency_key}"


async def start_idempotent_job(
    redis,
    system: System,
    idempotency_key: str,
    request_hash: str,
    namespace: str = "subscription",
):
    redis_key = _build_idempotency_key(namespace, system, idempotency_key)
    placeholder = json.dumps(
        {
            "request_hash": request_hash,
            "job_id": None,
        }
    )
    created = await redis.set(
        redis_key,
        placeholder,
        ex=settings.SUBSCRIPTION_IDEMPOTENCY_TTL_SECONDS,
        nx=True,
    )

    if created:
        return None

    raw_record = await redis.get(redis_key)

    if raw_record is None:
        return None

    if isinstance(raw_record, bytes):
        raw_record = raw_record.decode()

    record = json.loads(raw_record)
    if record["request_hash"] != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A mesma chave de idempotência já foi usada com outro payload.",
        )

    if record["job_id"] is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma operação em andamento com essa chave de idempotência.",
        )

    return record


async def save_idempotent_job(
    redis,
    system: System,
    idempotency_key: str,
    request_hash: str,
    job_id: str,
    namespace: str = "subscription",
):
    redis_key = _build_idempotency_key(namespace, system, idempotency_key)
    record = json.dumps(
        {
            "request_hash": request_hash,
            "job_id": job_id,
        }
    )
    await redis.setex(redis_key, settings.SUBSCRIPTION_IDEMPOTENCY_TTL_SECONDS, record)


async def clear_idempotent_job(redis, system: System, idempotency_key: str, namespace: str = "subscription"):
    redis_key = _build_idempotency_key(namespace, system, idempotency_key)
    await redis.delete(redis_key)
