from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

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
from app.infra.interfaces.mercadopago_api import MercadoPagoAPI
from app.infra.interfaces.mercadopago_mappers import (
    MIN_PIX_EXPIRATION_MINUTES,
    billing_type_from_payment,
    excluded_payment_types,
    net_value,
    parse_datetime,
    payment_status_to_gateway,
    resolve_checkout_status,
)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


class MercadoPagoProvider(InterfaceGateway):
    def __init__(self, api: MercadoPagoAPI | None = None, *, now: Callable[[], datetime] | None = None):
        self.api = api or MercadoPagoAPI(settings.MERCADOPAGO_ACCESS_TOKEN or "", settings.MERCADOPAGO_BASE_URL)
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ----------------------------------------------------------------- customers

    @staticmethod
    def _customer_response(data: dict, *, name: str, email: str, external_reference: str | None) -> GetCustomerResponse:
        full_name = " ".join(part for part in (data.get("first_name"), data.get("last_name")) if part)
        return GetCustomerResponse(
            cus_id=str(data["id"]),
            name=full_name or name,
            email=data.get("email") or email,
            external_reference=data.get("description") or external_reference,
            deleted=False,
        )

    async def create_customer(self, name: str, cpfCnpj: str, email: str, external_reference: str) -> GetCustomerResponse:
        found = await self.api.get("/v1/customers/search", params={"email": email})
        results = found.get("results") or []
        if results:
            return self._customer_response(results[0], name=name, email=email, external_reference=external_reference)

        first_name, _, last_name = name.strip().partition(" ")
        created = await self.api.post(
            "/v1/customers",
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "identification": {"type": "CPF" if len(cpfCnpj) == 11 else "CNPJ", "number": cpfCnpj},
                "description": external_reference,
            },
        )
        return self._customer_response(created, name=name, email=email, external_reference=external_reference)

    async def get_customer(self, cus_id: str) -> GetCustomerResponse | None:
        data = await self.api.get(f"/v1/customers/{cus_id}")
        return self._customer_response(data, name="", email="", external_reference=None)

    # ------------------------------------------------------------------ checkout

    @staticmethod
    def _checkout_response(preference: dict, *, status: str) -> CreateCheckoutGatewayResponse:
        checkout_id = preference.get("id")
        checkout_url = preference.get("init_point")
        external_reference = preference.get("external_reference")
        if not checkout_id or not checkout_url or not external_reference:
            raise DomainError("Resposta de checkout do Mercado Pago incompleta.")
        return CreateCheckoutGatewayResponse(
            checkout_id=str(checkout_id),
            checkout_url=checkout_url,
            status=status,
            external_reference=external_reference,
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
        now = self._now()
        # Pix gerado vence junto com o checkout; o Pix exige ao menos 30 minutos.
        expires_at = _iso(now + timedelta(minutes=max(minutes_to_expire, MIN_PIX_EXPIRATION_MINUTES)))
        payload = {
            "external_reference": external_reference,
            "items": [
                {
                    "id": item["externalReference"],
                    "title": item["name"],
                    "description": item.get("description") or "",
                    "quantity": item["quantity"],
                    "unit_price": item["value"],
                    "currency_id": "BRL",
                }
                for item in items
            ],
            "back_urls": {
                "success": callback.get("successUrl"),
                "pending": callback.get("successUrl"),
                "failure": callback.get("cancelUrl"),
            },
            "auto_return": "approved",
            "expires": True,
            "expiration_date_from": _iso(now),
            "expiration_date_to": expires_at,
            "date_of_expiration": expires_at,
            "payment_methods": {"excluded_payment_types": excluded_payment_types(billing_types)},
        }
        response = await self.api.post("/checkout/preferences", payload, idempotency_key=external_reference)
        checkout = self._checkout_response(response, status="ACTIVE")
        if checkout.external_reference != external_reference:
            raise DomainError("Checkout do Mercado Pago retornou external_reference divergente.")
        return checkout

    async def get_checkout(self, checkout_id: str) -> CreateCheckoutGatewayResponse:
        preference = await self.api.get(f"/checkout/preferences/{checkout_id}")
        if str(preference.get("id")) != checkout_id:
            raise DomainError("Checkout do Mercado Pago retornou id divergente.")

        payments: list[dict] = []
        if preference.get("external_reference"):
            search = await self.api.get(
                "/v1/payments/search",
                params={"external_reference": preference["external_reference"], "sort": "date_created", "criteria": "desc"},
            )
            payments = search.get("results") or []

        status = resolve_checkout_status(
            preference,
            payments,
            now=self._now(),
            grace=timedelta(seconds=settings.MERCADOPAGO_CHECKOUT_EXPIRY_GRACE_SECONDS),
        )
        return self._checkout_response(preference, status=status)

    async def get_payment(self, payment_id: str) -> PaymentStatusGatewayResponse:
        payment = await self.api.get(f"/v1/payments/{payment_id}")
        approved_at = parse_datetime(payment.get("date_approved"))
        return PaymentStatusGatewayResponse(
            payment_id=str(payment["id"]),
            status=payment_status_to_gateway(payment.get("status")),
            value=Decimal(str(payment["transaction_amount"])),
            net_value=net_value(payment),
            due_date=None,
            payment_date=approved_at.date() if approved_at else None,
            invoice_url=None,
            billing_type=billing_type_from_payment(payment),
            external_reference=payment.get("external_reference"),
        )

    # ------------------------------------------------ assinaturas e webhook (Tasks 7-8)

    def normalize_webhook(self, payload: dict) -> WebhookPayload:
        raise NotImplementedError

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
        raise NotImplementedError

    async def get_subscription_payment(self, subscription_id: str) -> list[SubscriptionPaymentResponse]:
        raise NotImplementedError

    async def cancel_subscription(self, subscription_id: str) -> str:
        raise NotImplementedError

    async def verify_status(self, subscription_id: str) -> SubscriptionStatusResponse:
        raise NotImplementedError
