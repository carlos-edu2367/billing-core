import logging
from datetime import date
from decimal import Decimal

import httpx

from app.application.dtos.request.webhook import WebhookPayload
from app.application.interfaces.gateway_provider import (
    CreateCheckoutGatewayResponse,
    GetCustomerResponse,
    InterfaceGateway,
    PaymentStatusGatewayResponse,
    SubscriptionPaymentResponse,
    SubscriptionStatusResponse,
)
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.errors import DomainError
from app.infra.config import settings

logger = logging.getLogger(__name__)


class AsaasAPIError(Exception):
    """Erro retornado pelo Asaas com status HTTP e corpo da resposta."""

    def __init__(self, status_code: int, body: str, method: str, endpoint: str) -> None:
        super().__init__(f"Asaas {method} {endpoint} → {status_code}: {body}")
        self.status_code = status_code
        self.body = body
        self.method = method
        self.endpoint = endpoint


class AsaasAPI:
    def __init__(self, api_key: str, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "access_token": api_key,
            "Content-Type": "application/json",
            "User-Agent": "Neectify/1.0",
        }

    def _build_url(self, endpoint: str) -> str:
        normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{self.base_url}{normalized}"

    def _handle_error(self, response: httpx.Response, method: str, endpoint: str) -> None:
        """Loga o body completo do Asaas e levanta AsaasAPIError com os detalhes."""
        body = response.text
        logger.error(
            "asaas_api_error",
            extra={
                "extra_data": {
                    "method": method,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "response_body": body,
                }
            },
        )
        raise AsaasAPIError(
            status_code=response.status_code,
            body=body,
            method=method,
            endpoint=endpoint,
        )

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.get(self._build_url(endpoint), params=params)
            if response.is_error:
                self._handle_error(response, "GET", endpoint)
            return response.json()

    async def post(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.post(self._build_url(endpoint), json=payload)
            if response.is_error:
                self._handle_error(response, "POST", endpoint)
            return response.json()

    async def delete(self, endpoint: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.delete(self._build_url(endpoint))
            if response.is_error:
                self._handle_error(response, "DELETE", endpoint)
            return response.json()


class AsaasProvider(InterfaceGateway):
    def __init__(self):
        self.asaas = AsaasAPI(settings.ASAAS_API_TOKEN, settings.resolved_asaas_base_url)

    def normalize_webhook(self, payload: dict) -> WebhookPayload:
        if "details" in payload:
            return WebhookPayload.model_validate(payload)

        event = payload.get("event")
        source_event_id = payload.get("id")
        checkout = payload.get("checkout") or {}
        if checkout:
            value = checkout.get("value")
            if value is None:
                value = sum(
                    Decimal(str(item.get("value", 0))) * Decimal(str(item.get("quantity", 1)))
                    for item in checkout.get("items", [])
                )

            return WebhookPayload.model_validate(
                {
                    "event": event,
                    "source_event_id": source_event_id,
                    "details": {
                        "id": checkout.get("id"),
                        "status": checkout.get("status"),
                        "value": value,
                        "external_reference": checkout.get("externalReference"),
                    },
                }
            )

        payment = payload.get("payment") or {}
        subscription = payload.get("subscription") or {}

        normalized = {
            "event": event,
            "source_event_id": source_event_id,
            "details": {
                "id": payment.get("id") or payload.get("paymentId"),
                "subscription": payment.get("subscription") or subscription.get("id"),
                "status": payment.get("status") or subscription.get("status"),
                "value": payment.get("value"),
                "net_value": payment.get("netValue"),
                "payment_date": payment.get("paymentDate"),
                "external_reference": payment.get("externalReference"),
                "billing_type": payment.get("billingType") or subscription.get("billingType"),
            },
        }
        return WebhookPayload.model_validate(normalized)

    async def create_subscription(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        next_due_date: date,
        cycle: SubscriptionType,
        description: str,
        external_reference: str | None = None,
    ) -> str:
        if billing_type == PaymentType.DEBIT_CARD:
            raise DomainError("DEBIT_CARD não é suportado pelo Asaas para assinaturas recorrentes.")

        payload = {
            "customer": customer_provider_id,
            "billingType": billing_type.value,
            "value": float(value),
            "nextDueDate": next_due_date.isoformat(),
            "cycle": cycle.value,
            "description": description,
        }
        if external_reference:
            payload["externalReference"] = external_reference
        response = await self.asaas.post("/subscriptions", payload)
        return response["id"]

    async def get_subscription_payment(self, subscription_id: str) -> list[SubscriptionPaymentResponse]:
        response = await self.asaas.get(f"/subscriptions/{subscription_id}/payments")
        return [
            SubscriptionPaymentResponse(
                payment_id=item["id"],
                status=item["status"],
                due_date=date.fromisoformat(item["dueDate"]),
                value=Decimal(str(item["value"])),
                invoice_url=item.get("invoiceUrl"),
                billing_type=item["billingType"],
            )
            for item in response.get("data", [])
        ]

    async def cancel_subscription(self, subscription_id: str) -> str:
        await self.asaas.delete(f"/subscriptions/{subscription_id}")
        return subscription_id

    async def verify_status(self, subscription_id: str) -> SubscriptionStatusResponse:
        response = await self.asaas.get(f"/subscriptions/{subscription_id}")
        return SubscriptionStatusResponse(
            subscription_id=response["id"],
            status=response["status"],
            deleted=response["deleted"],
            next_due_date=date.fromisoformat(response["nextDueDate"]),
            value=Decimal(str(response["value"])),
            cycle=response["cycle"],
        )

    async def create_checkout(
        self,
        *,
        billing_types: list[str],
        charge_types: list[str],
        minutes_to_expire: int,
        external_reference: str,
        callback: dict,
        items: list[dict],
    ) -> CreateCheckoutGatewayResponse:
        payload = {
            "billingTypes": billing_types,
            "chargeTypes": charge_types,
            "minutesToExpire": minutes_to_expire,
            "externalReference": external_reference,
            "callback": callback,
            "items": items,
        }
        response = await self.asaas.post("/checkouts", payload)
        checkout = self._checkout_response(response)
        if checkout.external_reference != external_reference:
            raise DomainError("Checkout do Asaas retornou externalReference divergente.")

        return checkout

    async def get_checkout(self, checkout_id: str) -> CreateCheckoutGatewayResponse:
        response = await self.asaas.get(f"/checkouts/{checkout_id}")
        checkout = self._checkout_response(response)
        if checkout.checkout_id != checkout_id:
            raise DomainError("Checkout do Asaas retornou id divergente.")
        return checkout

    @staticmethod
    def _checkout_response(response: dict) -> CreateCheckoutGatewayResponse:
        checkout_id = response.get("id")
        checkout_url = response.get("link")
        status = response.get("status")
        response_external_reference = response.get("externalReference")
        if not checkout_id or not checkout_url or not status or not response_external_reference:
            raise DomainError("Resposta de checkout do Asaas incompleta.")
        return CreateCheckoutGatewayResponse(
            checkout_id=checkout_id,
            checkout_url=checkout_url,
            status=status,
            external_reference=response_external_reference,
        )

    async def get_payment(self, payment_id: str) -> PaymentStatusGatewayResponse:
        response = await self.asaas.get(f"/payments/{payment_id}")
        payment_date = response.get("paymentDate")
        return PaymentStatusGatewayResponse(
            payment_id=response["id"],
            status=response["status"],
            value=Decimal(str(response["value"])),
            net_value=Decimal(str(response["netValue"])) if response.get("netValue") is not None else None,
            due_date=date.fromisoformat(response["dueDate"]) if response.get("dueDate") else None,
            payment_date=date.fromisoformat(payment_date) if payment_date else None,
            invoice_url=response.get("invoiceUrl"),
            billing_type=response["billingType"],
            external_reference=response.get("externalReference"),
        )

    async def create_customer(
        self,
        name: str,
        cpfCnpj: str,
        email: str,
        external_reference: str,
    ) -> GetCustomerResponse:
        result = await self.asaas.get("/customers", params={"cpfCnpj": cpfCnpj})
        existing = [item for item in result.get("data", []) if not item.get("deleted")]

        if existing:
            found = existing[0]
            return GetCustomerResponse(
                cus_id=found["id"],
                name=found.get("name", name),
                email=found.get("email", email),
                external_reference=found.get("externalReference", external_reference),
                deleted=False,
            )

        response = await self.asaas.post(
            "/customers",
            {
                "name": name,
                "cpfCnpj": cpfCnpj,
                "email": email,
                "externalReference": external_reference,
            },
        )
        return GetCustomerResponse(
            cus_id=response["id"],
            name=response.get("name", name),
            email=response.get("email", email),
            external_reference=response.get("externalReference", external_reference),
            deleted=False,
        )

    async def get_customer(self, cus_id: str) -> GetCustomerResponse | None:
        response = await self.asaas.get(f"/customers/{cus_id}")
        if response.get("deleted"):
            return None

        return GetCustomerResponse(
            cus_id=response["id"],
            name=response["name"],
            email=response["email"],
            external_reference=response.get("externalReference"),
            deleted=False,
        )
