from enum import Enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    code: str = Field(..., examples=["validation_error"])
    message: str = Field(..., examples=["Dados invalidos."])
    request_id: str | None = Field(default=None, examples=["f5b83bd7-1a1a-4c56-a9cf-262eb8f88721"])


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AcceptedJobResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "c9bf9e57-1685-4c89-bafb-ff5af830be8a",
                "message": "Assinatura enviada para processamento.",
            }
        }
    )

    job_id: str = Field(description="Identificador do job enfileirado para acompanhamento posterior.")
    message: str = Field(description="Mensagem resumindo se o processamento foi aceito ou reaproveitado por idempotência.")


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "c9bf9e57-1685-4c89-bafb-ff5af830be8a",
                "status": "retrying",
                "job_name": "create_subscription_worker",
                "attempt": 2,
                "max_tries": 3,
                "request_id": "f5b83bd7-1a1a-4c56-a9cf-262eb8f88721",
                "created_at": "2026-04-23T19:00:00+00:00",
                "started_at": "2026-04-23T19:00:02+00:00",
                "finished_at": None,
                "error_code": "worker_retry",
                "error_message": "TimeoutError",
            }
        }
    )

    job_id: str = Field(description="Identificador do job.")
    status: JobStatus = Field(description="Estado atual do processamento assíncrono.")
    job_name: str | None = Field(default=None, description="Nome técnico da rotina executada pelo worker.")
    attempt: int | None = Field(default=None, description="Tentativa atual de execução.")
    max_tries: int | None = Field(default=None, description="Número máximo configurado de tentativas.")
    request_id: str | None = Field(default=None, description="Correlation ID associado à requisição original.")
    created_at: datetime | None = Field(default=None, description="Data/hora em que o job foi registrado.")
    started_at: datetime | None = Field(default=None, description="Data/hora em que o worker iniciou a execução.")
    finished_at: datetime | None = Field(default=None, description="Data/hora de finalização do job, quando disponível.")
    error_code: str | None = Field(default=None, description="Código de erro operacional, quando houver.")
    error_message: str | None = Field(default=None, description="Mensagem de erro registrada na tentativa atual/final.")
    result: dict[str, Any] | None = Field(default=None, description="Resultado publico retornado pelo worker quando o job conclui com sucesso.")


def build_error_responses(*status_codes: int) -> dict[int, dict[str, object]]:
    descriptions = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        415: "Unsupported Media Type",
        409: "Conflict",
        413: "Payload Too Large",
        429: "Too Many Requests",
        422: "Validation Error",
        500: "Internal Server Error",
    }
    return {
        status_code: {
            "model": ErrorResponse,
            "description": descriptions[status_code],
        }
        for status_code in status_codes
    }
