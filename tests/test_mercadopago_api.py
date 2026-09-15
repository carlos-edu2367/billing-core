import httpx
import pytest

from app.infra.interfaces.mercadopago_api import MercadoPagoAPI, MercadoPagoAPIError


async def test_post_sends_bearer_token_and_idempotency_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["idempotency"] = request.headers.get("X-Idempotency-Key")
        seen["url"] = str(request.url)
        return httpx.Response(201, json={"id": "pref-1"})

    api = MercadoPagoAPI("TEST-token", "https://api.mercadopago.com/", transport=httpx.MockTransport(handler))

    body = await api.post("/checkout/preferences", {"items": []}, idempotency_key="checkout:marketfy:1")

    assert body == {"id": "pref-1"}
    assert seen == {
        "authorization": "Bearer TEST-token",
        "idempotency": "checkout:marketfy:1",
        "url": "https://api.mercadopago.com/checkout/preferences",
    }


async def test_get_forwards_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    api = MercadoPagoAPI("TEST-token", "https://api.mercadopago.com", transport=httpx.MockTransport(handler))

    await api.get("/v1/payments/search", params={"external_reference": "checkout:marketfy:1"})

    assert seen["params"] == {"external_reference": "checkout:marketfy:1"}


async def test_error_response_raises_gateway_error_with_status_and_body():
    api = MercadoPagoAPI(
        "TEST-token",
        "https://api.mercadopago.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"message": "invalid"})),
    )

    with pytest.raises(MercadoPagoAPIError) as exc_info:
        await api.put("/preapproval/abc", {"status": "cancelled"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.is_client_error is True
    assert "invalid" in exc_info.value.body
    assert str(exc_info.value).startswith("MercadoPago PUT /preapproval/abc → 400")
