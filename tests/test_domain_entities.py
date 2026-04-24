from datetime import datetime, timezone
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
