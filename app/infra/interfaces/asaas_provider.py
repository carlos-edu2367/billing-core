from datetime import date
from decimal import Decimal

import httpx

from app.application.dtos.request.webhook import WebhookPayload
from app.application.interfaces.gateway_provider import (
    CreatePaymentGatewayResponse,
    GetCustomerResponse,
    InterfaceGateway,
    PaymentStatusGatewayResponse,
    SubscriptionPaymentResponse,
    SubscriptionStatusResponse,
)
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_type import SubscriptionType
from app.infra.config import settings


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

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.get(self._build_url(endpoint), params=params)
            response.raise_for_status()
            return response.json()

    async def post(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.post(self._build_url(endpoint), json=payload)
            response.raise_for_status()
            return response.json()

    async def delete(self, endpoint: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0, headers=self.headers) as client:
            response = await client.delete(self._build_url(endpoint))
            response.raise_for_status()
            return response.json()


class AsaasProvider(InterfaceGateway):
    def __init__(self):
        self.asaas = AsaasAPI(settings.ASAAS_API_TOKEN, settings.resolved_asaas_base_url)

    def normalize_webhook(self, payload: dict) -> WebhookPayload:
        if "details" in payload:
            return WebhookPayload.model_validate(payload)

        event = payload.get("event")
        source_event_id = payload.get("id")
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
    ) -> str:
        payload = {
            "customer": customer_provider_id,
            "billingType": billing_type.value if billing_type != PaymentType.DEBIT_CARD else PaymentType.UNDEFINED.value,
            "value": float(value),
            "nextDueDate": next_due_date.isoformat(),
            "cycle": cycle.value,
            "description": description,
        }
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

    async def create_payment(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        due_date: date,
        description: str,
        external_reference: str,
    ) -> CreatePaymentGatewayResponse:
        payload = {
            "customer": customer_provider_id,
            "billingType": billing_type.value,
            "value": float(value),
            "dueDate": due_date.isoformat(),
            "description": description,
            "externalReference": external_reference,
        }
        response = await self.asaas.post("/payments", payload)
        return CreatePaymentGatewayResponse(
            payment_id=response["id"],
            status=response["status"],
            value=Decimal(str(response["value"])),
            due_date=date.fromisoformat(response["dueDate"]),
            invoice_url=response.get("invoiceUrl"),
            billing_type=response["billingType"],
            external_reference=response.get("externalReference"),
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
            customer_id = existing[0]["id"]
        else:
            response = await self.asaas.post(
                "/customers",
                {
                    "name": name,
                    "cpfCnpj": cpfCnpj,
                    "email": email,
                    "externalReference": external_reference,
                },
            )
            customer_id = response["id"]

        return GetCustomerResponse(
            cus_id=customer_id,
            name=name,
            email=email,
            external_reference=external_reference,
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
