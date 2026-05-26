from dataclasses import dataclass
import hashlib
import hmac
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request, status

from app.domain.enums.system import System
from app.infra.config import settings
from app.web.dependencies.common import get_redis_pool


@dataclass(frozen=True)
class AuthContext:
    system: System
    scopes: frozenset[str]


@dataclass(frozen=True)
class WebhookValidationResult:
    raw_body: bytes
    replay_key: str
    duplicate: bool = False


def _parse_system(raw_system: str) -> System:
    normalized = raw_system.strip()

    for system in System:
        if normalized.lower() in {system.name.lower(), system.value.lower()}:
            return system

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sistema invalido.",
    )


def require_internal_auth(*required_scopes: str) -> Callable:
    async def dependency(
        request: Request,
        x_system: str = Header(
            ...,
            alias="X-System",
            description=(
                "Identifica o sistema interno chamador. "
                "Use um valor configurado em `INTERNAL_API_CLIENTS`, como `neectify_shop`."
            ),
            examples=["neectify_shop"],
        ),
        x_api_key: str = Header(
            ...,
            alias="X-API-Key",
            description=(
                "Chave de autenticação server-to-server do sistema informado em `X-System`. "
                "A chave precisa ter os scopes necessários para o endpoint."
            ),
            examples=["sk_live_internal_xpto123"],
        ),
    ) -> AuthContext:
        system = _parse_system(x_system)
        client = settings.INTERNAL_API_CLIENTS.get(system.value)
        configured_api_key = client.api_key.strip() if client else ""

        if client is None or not configured_api_key or not hmac.compare_digest(x_api_key, configured_api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciais invalidas.",
            )

        scopes = frozenset(client.scopes)
        missing_scopes = [scope for scope in required_scopes if scope not in scopes]
        if missing_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Escopo insuficiente.",
            )

        auth = AuthContext(system=system, scopes=scopes)
        request.state.auth = auth
        return auth

    return dependency


async def validate_asaas_webhook(
    request: Request,
    redis=Depends(get_redis_pool),
    asaas_access_token: str | None = Header(
        default=None,
        alias="asaas-access-token",
        description=(
            "Secret compartilhado com o Asaas para autenticar o webhook. "
            "Deve corresponder a `ASAAS_WEBHOOK_SECRET`."
        ),
        examples=["whsec_xpto123"],
    ),
):
    content_type = request.headers.get("content-type", "")
    if content_type and "application/json" not in content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Webhook deve usar content-type application/json.",
        )

    if not asaas_access_token or not hmac.compare_digest(asaas_access_token, settings.ASAAS_WEBHOOK_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook nao autorizado.",
        )

    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook e obrigatorio.",
        )

    if len(raw_body) > settings.MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload do webhook excede o tamanho maximo permitido.",
        )

    replay_hash = hashlib.sha256(raw_body).hexdigest()
    replay_key = f"billing_core:webhook_replay:{replay_hash}"
    existing_replay = await redis.get(replay_key)

    if existing_replay is not None:
        return WebhookValidationResult(raw_body=raw_body, replay_key=replay_key, duplicate=True)

    return WebhookValidationResult(raw_body=raw_body, replay_key=replay_key)
