from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.entities.customer import Customer
from app.domain.entities.payment import Payment
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.domain.errors import DomainError
from app.domain.value_objects.cpf import CPF
from app.domain.value_objects.email import Email


def test_customer_requires_single_primary_document():
    with pytest.raises(DomainError):
        Customer(
            nome="Carlos",
            email=Email("carlos@example.com"),
            system_customer_id="cus-1",
            system=System.NEECTIFY_SHOP,
        )


def test_subscription_cannot_reactivate_canceled_subscription():
    subscription = Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.CANCELED,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        value=Decimal("99.90"),
    )

    with pytest.raises(DomainError):
        subscription.mark_as_paid(datetime.now(timezone.utc))


def test_subscription_request_cancellation_sets_pending_metadata():
    subscription = Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.ACTIVE,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        value=Decimal("99.90"),
    )

    subscription.request_cancellation(reason="pedido do cliente", job_id="job-1")

    assert subscription.status == SubscriptionStatus.CANCELLATION_PENDING
    assert subscription.cancellation_reason == "pedido do cliente"
    assert subscription.cancellation_job_id == "job-1"
    assert subscription.cancellation_requested_at is not None


def test_subscription_cancel_is_idempotent_after_pending_request():
    subscription = Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.ACTIVE,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        value=Decimal("99.90"),
    )

    subscription.request_cancellation(reason="pedido do cliente", job_id="job-1")
    subscription.cancel(reason="pedido do cliente", job_id="job-1")
    canceled_at = subscription.cancelled_at
    subscription.cancel(reason="pedido do cliente", job_id="job-1")

    assert subscription.status == SubscriptionStatus.CANCELED
    assert subscription.cancelled_at == canceled_at


def test_payment_mark_as_paid_is_idempotent_for_paid_payment():
    payment = Payment.create_subscription_payment(
        description="Pagamento da assinatura",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="sub-1:pay-1",
        provider_payment_id="pay-1",
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id="sub-id",
    )
    paid_at = datetime.now(timezone.utc)

    payment.mark_as_paid(payment_date=paid_at, net_value=Decimal("94.00"))
    payment.mark_as_paid(payment_date=paid_at, net_value=Decimal("94.00"))

    assert payment.payment_status == PaymentStatus.PAID
    assert payment.paid_date == paid_at
    assert payment.net_value == Decimal("94.00")


<<<<<<< HEAD
def test_standalone_payment_can_be_confirmed_and_received():
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="pay_123",
        value=Decimal("79.90"),
        from_system=System.NEECTIFY_SHOP,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=date(2026, 6, 10),
        external_reference="payment:neectify_shop:order-123",
    )

    payment.mark_as_confirmed(datetime(2026, 6, 10, tzinfo=timezone.utc), Decimal("77.90"))
    assert payment.payment_status == PaymentStatus.CONFIRMED
    assert payment.net_value == Decimal("77.90")

    payment.mark_as_paid(datetime(2026, 6, 11, tzinfo=timezone.utc), Decimal("77.90"))
    assert payment.payment_status == PaymentStatus.PAID
    assert payment.paid_date == datetime(2026, 6, 11, tzinfo=timezone.utc)


def test_standalone_payment_can_be_marked_overdue_before_payment():
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="pay_123",
        value=Decimal("79.90"),
        from_system=System.NEECTIFY_SHOP,
        checkout_link="https://www.asaas.com/i/pay_123",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=date(2026, 6, 10),
        external_reference="payment:neectify_shop:order-123",
    )

    payment.mark_as_overdue()

    assert payment.payment_status == PaymentStatus.OVERDUE
=======
def test_subscription_monthly_mark_as_paid_advances_by_one_calendar_month():
    base = datetime(2026, 1, 31, tzinfo=timezone.utc)
    subscription = Subscription(
        initial_date=base,
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.PENDING,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=base,
        value=Decimal("99.90"),
    )

    subscription.mark_as_paid(base)

    # Jan 31 + 1 mês = Feb 28 (não Feb 31, que não existe)
    assert subscription.expires_at.month == 2
    assert subscription.expires_at.day == 28
    assert subscription.expires_at.year == 2026


def test_subscription_monthly_mark_as_paid_regular_day():
    base = datetime(2026, 3, 15, tzinfo=timezone.utc)
    subscription = Subscription(
        initial_date=base,
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.PENDING,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=base,
        value=Decimal("99.90"),
    )

    subscription.mark_as_paid(base)

    assert subscription.expires_at == datetime(2026, 4, 15, tzinfo=timezone.utc)


def test_subscription_yearly_mark_as_paid_advances_by_one_calendar_year():
    base = datetime(2024, 2, 29, tzinfo=timezone.utc)  # ano bissexto
    subscription = Subscription(
        initial_date=base,
        description="Plano Anual",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=SubscriptionStatus.PENDING,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.YEARLY,
        expires_at=base,
        value=Decimal("999.90"),
    )

    subscription.mark_as_paid(base)

    # Feb 29 2024 + 1 ano = Feb 28 2025 (2025 não é bissexto)
    assert subscription.expires_at == datetime(2025, 2, 28, tzinfo=timezone.utc)
>>>>>>> 8d53df5827d324ba4f83016e602e7166833db3f4


def test_customer_bind_provider_customer_sets_binding():
    customer = Customer(
        nome="Carlos",
        email=Email("carlos@example.com"),
        cpf=CPF("39053344705"),
        system_customer_id="cus-1",
        system=System.NEECTIFY_SHOP,
    )

    customer.bind_provider_customer("asaas_cus_1")

    assert customer.has_provider_binding() is True
    assert customer.provider_customer_id == "asaas_cus_1"
