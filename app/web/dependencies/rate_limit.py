from typing import Callable

from fastapi import Depends, HTTPException, Request, status

from app.infra.config import settings
from app.web.dependencies.common import get_redis_pool


def _resolve_client_identity(request: Request) -> str:
    if settings.TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

    return request.client.host if request.client else "unknown"


def rate_limit(
    key_prefix: str,
    limit: int,
    window_seconds: int,
    use_system: bool = False,
) -> Callable:
    async def dependency(
        request: Request,
        redis=Depends(get_redis_pool),
    ):
        if use_system:
            auth = getattr(request.state, "auth", None)
            if auth is None:
                raw_system = request.headers.get("X-System")
                if not raw_system:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Credenciais invalidas.",
                    )
                identity = raw_system.strip().lower()
            else:
                identity = auth.system.value
        else:
            identity = _resolve_client_identity(request)

        key = f"billing_core:rate_limit:{key_prefix}:{identity}"
        current = await redis.incr(key)

        if current == 1:
            await redis.expire(key, window_seconds)

        if current > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Limite de requisicoes excedido.",
            )

    return dependency


def internal_rate_limit() -> Callable:
    return rate_limit(
        key_prefix="internal",
        limit=settings.INTERNAL_RATE_LIMIT_REQUESTS,
        window_seconds=settings.INTERNAL_RATE_LIMIT_WINDOW_SECONDS,
        use_system=True,
    )


def webhook_rate_limit() -> Callable:
    return rate_limit(
        key_prefix="webhook",
        limit=settings.WEBHOOK_RATE_LIMIT_REQUESTS,
        window_seconds=settings.WEBHOOK_RATE_LIMIT_WINDOW_SECONDS,
        use_system=False,
    )
