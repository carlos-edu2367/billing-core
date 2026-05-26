from fastapi import APIRouter, Depends, HTTPException, status

from app.infra.jobs import get_job_metadata
from app.web.dependencies.common import get_redis_pool
from app.web.dependencies.rate_limit import internal_rate_limit
from app.web.dependencies.security import AuthContext, require_internal_auth
from app.web.schemas.common import JobStatus, JobStatusResponse, build_error_responses


router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    summary="Consultar job",
    description="""
Consulta o status de um job pertencente ao sistema autenticado.

### Quando usar
- Após `POST /v1/subscriptions`
- Após recebimento assíncrono de algum processo que retornou `job_id`

### Estados possíveis
- `queued`: job aceito e aguardando execução
- `processing`: worker em execução
- `retrying`: tentativa falhou e nova execução será feita
- `completed`: job concluído
- `failed`: job esgotou tentativas ou falhou definitivamente

### Headers obrigatórios
- `X-System`
- `X-API-Key`
""",
    responses=build_error_responses(401, 403, 404, 429, 500),
)
async def get_job_status(
    job_id: str,
    auth: AuthContext = Depends(require_internal_auth("jobs:read")),
    _rate_limiter=Depends(internal_rate_limit()),
    redis=Depends(get_redis_pool),
):
    owner_key = f"billing_core:job_owner:{job_id}"
    owner_system = await redis.get(owner_key)
    metadata = await get_job_metadata(redis, job_id)

    if owner_system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job nao encontrado.",
        )

    if isinstance(owner_system, bytes):
        owner_system = owner_system.decode()

    if owner_system != auth.system.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sistema sem permissao para consultar esse job.",
        )

    if metadata:
        return JobStatusResponse(
            job_id=job_id,
            status=JobStatus(metadata.get("status", JobStatus.PROCESSING.value)),
            job_name=metadata.get("job_name"),
            attempt=int(metadata["attempt"]) if metadata.get("attempt") else None,
            max_tries=int(metadata["max_tries"]) if metadata.get("max_tries") else None,
            request_id=metadata.get("request_id") or None,
            created_at=metadata.get("created_at") or None,
            started_at=metadata.get("started_at") or None,
            finished_at=metadata.get("finished_at") or None,
            error_code=metadata.get("error_code") or None,
            error_message=metadata.get("error_message") or None,
        )

    result = await redis.get(f"arq:result:{job_id}")
    if result:
        return {"job_id": job_id, "status": JobStatus.COMPLETED}

    score = await redis.zscore("arq:queue", job_id)
    if score is not None:
        return {"job_id": job_id, "status": JobStatus.QUEUED}

    failed = await redis.sismember("arq:failed", job_id)
    if failed:
        return {"job_id": job_id, "status": JobStatus.FAILED}

    return {"job_id": job_id, "status": JobStatus.PROCESSING}
