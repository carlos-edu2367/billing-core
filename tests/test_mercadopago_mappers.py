from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.application.dtos.request.webhook import EventType
from app.domain.enums.subscription_type import SubscriptionType
from app.infra.interfaces import mercadopago_mappers as m

EXPIRATION = "2026-09-14T12:30:00.000+00:00"
EXPIRES_AT = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
GRACE = timedelta(minutes=5)


def make_payment(**overrides):
    base = {
        "id": 123456,
        "status": "approved",
        "status_detail": "accredited",
        "date_approved": "2026-09-14T12:10:06.000-03:00",
        "payment_type_id": "bank_transfer",
        "transaction_amount": 72.0,
        "transaction_details": {"net_received_amount": 71.28},
        "external_reference": "checkout:marketfy:order-123",
    }
    return base | overrides


@pytest.mark.parametrize(
    ("status", "expected"),
    [("approved", "RECEIVED"), ("authorized", "CONFIRMED"), ("refunded", "REFUNDED"),
     ("charged_back", "CHARGEBACK_REQUESTED"), ("rejected", "REJECTED"), (None, "")],
)
def test_payment_status_to_gateway(status, expected):
    assert m.payment_status_to_gateway(status) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [("authorized", "ACTIVE"), ("pending", "PENDING"), ("paused", "PAUSED"), ("cancelled", "CANCELED")],
)
def test_preapproval_status_to_gateway(status, expected):
    assert m.preapproval_status_to_gateway(status) == expected


def test_cycles_map_to_months_and_back():
    assert m.FREQUENCY_MONTHS_BY_CYCLE[SubscriptionType.SEMIANNUAL] == 6
    assert m.CYCLE_BY_FREQUENCY_MONTHS[12] == "YEARLY"


def test_excluded_payment_types_keeps_only_requested_billing_types():
    excluded = m.excluded_payment_types(["PIX", "CREDIT_CARD"])

    assert {"id": "bank_transfer"} not in excluded
    assert {"id": "credit_card"} not in excluded
    assert {"id": "ticket"} in excluded
    assert {"id": "debit_card"} in excluded


def test_billing_type_and_net_value_from_payment():
    payment = make_payment()

    assert m.billing_type_from_payment(payment) == "PIX"
    assert m.billing_type_from_payment(make_payment(payment_type_id="account_money")) == "UNDEFINED"
    assert m.net_value(payment) == Decimal("71.28")


@pytest.mark.parametrize(
    ("payments", "now", "expected"),
    [
        ([make_payment(status="approved")], EXPIRES_AT + timedelta(hours=1), "PAID"),
        ([], EXPIRES_AT - timedelta(minutes=1), "ACTIVE"),
        ([], EXPIRES_AT + timedelta(minutes=4), "ACTIVE"),
        ([], EXPIRES_AT + timedelta(minutes=6), "EXPIRED"),
        ([make_payment(status="cancelled")], EXPIRES_AT + timedelta(minutes=6), "EXPIRED"),
        ([make_payment(status="in_process")], EXPIRES_AT + timedelta(minutes=6), "ACTIVE"),
    ],
)
def test_resolve_checkout_status(payments, now, expected):
    preference = {"id": "pref-1", "expires": True, "expiration_date_to": EXPIRATION}

    assert m.resolve_checkout_status(preference, payments, now=now, grace=GRACE) == expected


def test_resolve_checkout_status_without_expiration_stays_active():
    assert m.resolve_checkout_status({"expires": False}, [], now=EXPIRES_AT + timedelta(days=9), grace=GRACE) == "ACTIVE"


def test_approved_checkout_payment_becomes_checkout_paid():
    payload = m.payment_notification_to_webhook(make_payment())

    assert payload.event == EventType.CHECKOUT_PAID
    assert payload.source_event_id == "payment:123456:approved"
    assert payload.details.id == "123456"
    assert payload.details.subscription is None
    assert payload.details.external_reference == "checkout:marketfy:order-123"
    assert payload.details.value == Decimal("72.0")
    assert payload.details.net_value == Decimal("71.28")
    assert payload.details.payment_date == datetime(2026, 9, 14, 15, 10, 6, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("status", "event"),
    [("refunded", EventType.PAYMENT_REFUNDED), ("charged_back", EventType.PAYMENT_CHARGEBACK_REQUESTED)],
)
def test_checkout_payment_reversal_maps_to_payment_event(status, event):
    payload = m.payment_notification_to_webhook(make_payment(status=status))

    assert payload.event == event
    assert payload.details.status == m.payment_status_to_gateway(status)


@pytest.mark.parametrize("status", ["pending", "rejected", "cancelled", "in_process"])
def test_non_final_checkout_payment_is_ignored(status):
    assert m.payment_notification_to_webhook(make_payment(status=status)) is None


def test_subscription_payment_refund_uses_subscription_id_from_payment():
    payment = make_payment(
        status="refunded",
        external_reference="sub_marketfy_1",
        point_of_interaction={"transaction_data": {"subscription_id": "2c938084726fca48"}},
    )

    payload = m.payment_notification_to_webhook(payment)

    assert payload.event == EventType.PAYMENT_REFUNDED
    assert payload.details.subscription == "2c938084726fca48"


def test_subscription_payment_approval_via_payment_topic_is_ignored():
    payment = make_payment(external_reference="sub_marketfy_1", payment_type_id="credit_card")

    assert m.payment_notification_to_webhook(payment) is None


def test_approved_authorized_payment_becomes_payment_received():
    invoice = {"id": 7001, "preapproval_id": "2c938084726fca48", "status": "processed", "payment": {"id": 123456, "status": "approved"}}

    payload = m.authorized_payment_to_webhook(invoice, make_payment(payment_type_id="credit_card", external_reference="sub_marketfy_1"))

    assert payload.event == EventType.PAYMENT_RECEIVED
    assert payload.source_event_id == "authorized_payment:7001:approved"
    assert payload.details.id == "123456"
    assert payload.details.subscription == "2c938084726fca48"
    assert payload.details.billing_type == "CREDIT_CARD"
    assert payload.details.status == "RECEIVED"


def test_authorized_payment_without_payment_or_with_rejection_is_ignored():
    invoice = {"id": 7001, "preapproval_id": "2c938084726fca48", "status": "recycling"}

    assert m.authorized_payment_to_webhook(invoice, None) is None
    assert m.authorized_payment_to_webhook(invoice, make_payment(status="rejected")) is None


def test_cancelled_preapproval_becomes_subscription_inactivated():
    payload = m.preapproval_notification_to_webhook({"id": "2c938084726fca48", "status": "cancelled"})

    assert payload.event == EventType.SUBSCRIPTION_INACTIVATED
    assert payload.source_event_id == "preapproval:2c938084726fca48:cancelled"
    assert payload.details.subscription == "2c938084726fca48"


@pytest.mark.parametrize("status", ["pending", "authorized", "paused"])
def test_other_preapproval_statuses_are_ignored(status):
    assert m.preapproval_notification_to_webhook({"id": "2c938084726fca48", "status": status}) is None
