from datetime import datetime, timezone
from typing import Any

from app.infra.config import settings


JOB_META_PREFIX = "billing_core:job_meta"
DEAD_LETTER_PREFIX = "billing_core:dead_letter"


def _serialize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def build_job_meta_key(job_id: str) -> str:
    return f"{JOB_META_PREFIX}:{job_id}"


async def update_job_metadata(redis, job_id: str, **fields: Any) -> None:
    payload = {key: _serialize_value(value) for key, value in fields.items()}
    if payload:
        await redis.hset(build_job_meta_key(job_id), mapping=payload)
    await redis.expire(build_job_meta_key(job_id), settings.JOB_METADATA_TTL_SECONDS)


async def get_job_metadata(redis, job_id: str) -> dict[str, str]:
    raw = await redis.hgetall(build_job_meta_key(job_id))
    if not raw:
        return {}

    decoded: dict[str, str] = {}
    for key, value in raw.items():
        decoded_key = key.decode() if isinstance(key, bytes) else str(key)
        decoded_value = value.decode() if isinstance(value, bytes) else str(value)
        decoded[decoded_key] = decoded_value
    return decoded


async def register_dead_letter(redis, job_name: str, job_id: str) -> None:
    dead_letter_key = f"{DEAD_LETTER_PREFIX}:{job_name}"
    score = datetime.now(timezone.utc).timestamp()
    await redis.zadd(dead_letter_key, {job_id: score})
    await redis.expire(dead_letter_key, settings.WORKER_DEAD_LETTER_TTL_SECONDS)
