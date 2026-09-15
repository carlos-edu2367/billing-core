import logging

import httpx

from app.application.interfaces.gateway_provider import GatewayAPIError

logger = logging.getLogger(__name__)


class MercadoPagoAPIError(GatewayAPIError):
    """Erro retornado pela API do Mercado Pago com status HTTP e corpo da resposta."""

    provider_label = "MercadoPago"


class MercadoPagoAPI:
    def __init__(
        self,
        access_token: str,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Neectify/1.0",
        }

    def _build_url(self, endpoint: str) -> str:
        normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{self.base_url}{normalized}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        headers = dict(self.headers)
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, transport=self.transport) as client:
            response = await client.request(method, self._build_url(endpoint), json=json, params=params)

        if response.is_error:
            logger.error(
                "mercadopago_api_error",
                extra={
                    "extra_data": {
                        "method": method,
                        "endpoint": endpoint,
                        "status_code": response.status_code,
                        "response_body": response.text,
                    }
                },
            )
            raise MercadoPagoAPIError(
                status_code=response.status_code,
                body=response.text,
                method=method,
                endpoint=endpoint,
            )

        return response.json() if response.content else {}

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, payload: dict, idempotency_key: str | None = None) -> dict:
        return await self._request("POST", endpoint, json=payload, idempotency_key=idempotency_key)

    async def put(self, endpoint: str, payload: dict) -> dict:
        return await self._request("PUT", endpoint, json=payload)
