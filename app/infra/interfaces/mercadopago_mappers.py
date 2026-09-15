"""Traducao dos recursos do Mercado Pago para o vocabulario que os use cases ja usam.

Reconciliacao, cancelamento e processamento de webhook comparam os status do Asaas
(ACTIVE, RECEIVED, CONFIRMED, CHECKOUT_PAID...). O adapter do Mercado Pago entrega
esses mesmos valores para que nada acima dele precise mudar.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from app.application.dtos.request.webhook import EventType, WebhookPayload
from app.domain.enums.subscription_type import SubscriptionType

CHECKOUT_REFERENCE_PREFIX = "checkout:"
# Vencimento minimo aceito pelo Pix no Mercado Pago.
MIN_PIX_EXPIRATION_MINUTES = 30

FREQUENCY_MONTHS_BY_CYCLE = {
    SubscriptionType.MONTHLY: 1,
    SubscriptionType.SEMIANNUAL: 6,
    SubscriptionType.YEARLY: 12,
}
CYCLE_BY_FREQUENCY_MONTHS = {months: cycle.value for cycle, months in FREQUENCY_MONTHS_BY_CYCLE.items()}

BILLING_TYPE_BY_PAYMENT_TYPE_ID = {
    "credit_card": "CREDIT_CARD",
    "debit_card": "DEBIT_CARD",
    "bank_transfer": "PIX",
    "ticket": "BOLETO",
}
PAYMENT_TYPE_ID_BY_BILLING_TYPE = {value: key for key, value in BILLING_TYPE_BY_PAYMENT_TYPE_ID.items()}
# Tipos que o Checkout Pro pode oferecer; ficam disponiveis so quando pedidos em billing_types.
EXCLUDABLE_PAYMENT_TYPE_IDS = ("credit_card", "debit_card", "bank_transfer", "ticket", "atm", "prepaid_card")

_PAYMENT_STATUS_TO_GATEWAY = {
    "approved": "RECEIVED",
    "authorized": "CONFIRMED",
    "refunded": "REFUNDED",
    "charged_back": "CHARGEBACK_REQUESTED",
}
_PREAPPROVAL_STATUS_TO_GATEWAY = {
    "authorized": "ACTIVE",
    "pending": "PENDING",
    "paused": "PAUSED",
    "cancelled": "CANCELED",
}
_REVERSAL_EVENTS = {
    "refunded": EventType.PAYMENT_REFUNDED,
    "charged_back": EventType.PAYMENT_CHARGEBACK_REQUESTED,
}
_UNSETTLED_PAYMENT_STATUSES = {"pending", "in_process", "authorized", "in_mediation"}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def payment_status_to_gateway(status: str | None) -> str:
    normalized = (status or "").lower()
    return _PAYMENT_STATUS_TO_GATEWAY.get(normalized, normalized.upper())


def preapproval_status_to_gateway(status: str | None) -> str:
    normalized = (status or "").lower()
    return _PREAPPROVAL_STATUS_TO_GATEWAY.get(normalized, normalized.upper())


def billing_type_from_payment(payment: dict) -> str:
    return BILLING_TYPE_BY_PAYMENT_TYPE_ID.get(payment.get("payment_type_id") or "", "UNDEFINED")


def excluded_payment_types(billing_types: list[str]) -> list[dict]:
    allowed = {PAYMENT_TYPE_ID_BY_BILLING_TYPE[item] for item in billing_types if item in PAYMENT_TYPE_ID_BY_BILLING_TYPE}
    return [{"id": type_id} for type_id in EXCLUDABLE_PAYMENT_TYPE_IDS if type_id not in allowed]


def _decimal(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def net_value(payment: dict) -> Decimal | None:
    return _decimal((payment.get("transaction_details") or {}).get("net_received_amount"))


def resolve_checkout_status(preference: dict, payments: list[dict], *, now: datetime, grace: timedelta) -> str:
    """Preferencia nao tem status: deriva ACTIVE/PAID/EXPIRED dos pagamentos e da vigencia."""
    statuses = {(payment.get("status") or "").lower() for payment in payments}
    if "approved" in statuses:
        return "PAID"

    expiration = parse_datetime(preference.get("expiration_date_to"))
    if not preference.get("expires") or expiration is None or now <= expiration + grace:
        return "ACTIVE"

    # Pagamento em analise ainda pode aprovar depois do prazo: so expira apos o desfecho.
    if statuses & _UNSETTLED_PAYMENT_STATUSES:
        return "ACTIVE"

    return "EXPIRED"


def _payment_details(payment: dict, *, subscription: str | None, status: str) -> dict:
    return {
        "id": str(payment["id"]),
        "subscription": subscription,
        "status": status,
        "value": _decimal(payment.get("transaction_amount")),
        "net_value": net_value(payment),
        "payment_date": payment.get("date_approved"),
        "external_reference": payment.get("external_reference"),
        "billing_type": billing_type_from_payment(payment),
    }


def subscription_id_from_payment(payment: dict) -> str | None:
    transaction_data = (payment.get("point_of_interaction") or {}).get("transaction_data") or {}
    return transaction_data.get("subscription_id")


def payment_notification_to_webhook(payment: dict) -> WebhookPayload | None:
    status = (payment.get("status") or "").lower()
    source_event_id = f"payment:{payment['id']}:{status}"
    external_reference = payment.get("external_reference") or ""

    if external_reference.startswith(CHECKOUT_REFERENCE_PREFIX):
        if status == "approved":
            event = EventType.CHECKOUT_PAID
            details = _payment_details(payment, subscription=None, status="PAID")
        elif status in _REVERSAL_EVENTS:
            event = _REVERSAL_EVENTS[status]
            details = _payment_details(payment, subscription=None, status=payment_status_to_gateway(status))
        else:
            # Recusa ou Pix vencido nao encerram o checkout: o comprador pode tentar de novo.
            return None
        return WebhookPayload.model_validate({"event": event, "source_event_id": source_event_id, "details": details})

    subscription_id = subscription_id_from_payment(payment)
    if status in _REVERSAL_EVENTS and subscription_id:
        return WebhookPayload.model_validate(
            {
                "event": _REVERSAL_EVENTS[status],
                "source_event_id": source_event_id,
                "details": _payment_details(payment, subscription=subscription_id, status=payment_status_to_gateway(status)),
            }
        )

    # Aprovacoes de assinatura chegam pelo topico subscription_authorized_payment.
    return None


def authorized_payment_to_webhook(invoice: dict, payment: dict | None) -> WebhookPayload | None:
    if not payment:
        return None

    status = (payment.get("status") or "").lower()
    if status == "approved":
        event = EventType.PAYMENT_RECEIVED
    elif status in _REVERSAL_EVENTS:
        event = _REVERSAL_EVENTS[status]
    else:
        return None

    return WebhookPayload.model_validate(
        {
            "event": event,
            "source_event_id": f"authorized_payment:{invoice['id']}:{status}",
            "details": _payment_details(
                payment,
                subscription=invoice.get("preapproval_id"),
                status=payment_status_to_gateway(status),
            ),
        }
    )


def preapproval_notification_to_webhook(preapproval: dict) -> WebhookPayload | None:
    if (preapproval.get("status") or "").lower() != "cancelled":
        return None

    preapproval_id = str(preapproval["id"])
    return WebhookPayload.model_validate(
        {
            "event": EventType.SUBSCRIPTION_INACTIVATED,
            "source_event_id": f"preapproval:{preapproval_id}:cancelled",
            "details": {"id": preapproval_id, "subscription": preapproval_id, "status": "CANCELED"},
        }
    )
