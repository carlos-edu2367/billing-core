from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from app.domain.errors import DomainError
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.movimentation_type import MovimentationType
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System


class Payment():
    def __init__(
        self,
        description: str | None,
        gateway: GatewayProvider,
        system_payment_id: str,
        provider_payment_id: str,
        value: Decimal,
        from_system: System,
        net_value: Decimal = Decimal(0),
        payment_status: PaymentStatus = PaymentStatus.PENDING,
        payment_type: PaymentType = PaymentType.PIX,
        paid_date: datetime = None,
        canceled_date: datetime = None,
        refunded_date: datetime = None,
        failed_date: datetime = None,
        movimentation_type: MovimentationType = MovimentationType.DEFAULT_PAYMENT,
        checkout_link: str = None,
        id: UUID = None,
        subscription_id: UUID = None,
        webhook_link: str = None,
    ):
        normalized_system_payment_id = (system_payment_id or "").strip()
        normalized_provider_payment_id = (provider_payment_id or "").strip()

        if not normalized_system_payment_id:
            raise DomainError("Pagamento precisa ter identificador do sistema.")

        if not normalized_provider_payment_id:
            raise DomainError("Pagamento precisa ter identificador do gateway.")

        if value <= 0:
            raise DomainError("Pagamento precisa ter valor positivo.")

        if net_value < 0:
            raise DomainError("Pagamento não pode ter valor líquido negativo.")

        self.description = description.strip() if description else None
        self.gateway = gateway
        self.system_payment_id = normalized_system_payment_id
        self.provider_payment_id = normalized_provider_payment_id
        self.value = value
        self.payment_status = payment_status
        self.payment_type = payment_type
        self.paid_date = paid_date
        self.from_system = from_system
        self.canceled_date = canceled_date
        self.refunded_date = refunded_date
        self.movimentation_type = movimentation_type
        self.failed_date = failed_date
        self.checkout_link = checkout_link
        self.id = id
        self.subscription_id = subscription_id
        self.webhook_link = webhook_link
        self.net_value = net_value

    @classmethod
    def create_subscription_payment(
        cls,
        *,
        description: str,
        gateway: GatewayProvider,
        system_payment_id: str,
        provider_payment_id: str,
        value: Decimal,
        from_system: System,
        subscription_id: UUID,
        checkout_link: str | None = None,
    ) -> "Payment":
        return cls(
            description=description,
            gateway=gateway,
            system_payment_id=system_payment_id,
            provider_payment_id=provider_payment_id,
            value=value,
            from_system=from_system,
            subscription_id=subscription_id,
            checkout_link=checkout_link,
            movimentation_type=MovimentationType.SUBSCRIPTION_PAYMENT,
        )

    def belongs_to_subscription(self, subscription_id: UUID) -> bool:
        return self.subscription_id == subscription_id

    def mark_as_canceled(self):
        if self.payment_status != PaymentStatus.PENDING:
            raise DomainError("Não é possível cancelar esse pagamento.")
        self.payment_status = PaymentStatus.CANCELED
        self.canceled_date = datetime.now(timezone.utc)

    def mark_as_paid(self, payment_date: datetime | None = None, net_value: Decimal | None = None):
        if self.payment_status == PaymentStatus.PAID:
            return

        if self.payment_status != PaymentStatus.PENDING:
            raise DomainError("Não é possível confirmar esse pagamento.")

        self.payment_status = PaymentStatus.PAID
        self.paid_date = payment_date or datetime.now(timezone.utc)
        if net_value is not None:
            if net_value < 0:
                raise DomainError("Pagamento não pode ter valor líquido negativo.")
            self.net_value = net_value

    def mark_as_refunded(self):
        if self.payment_status != PaymentStatus.PAID:
            raise DomainError("Não é possível estornar um pagamento não pago.")

        self.refunded_date = datetime.now(timezone.utc)
        self.payment_status = PaymentStatus.REFUNDED

    def mark_as_failed(self):
        if self.payment_status == PaymentStatus.PAID:
            raise DomainError("Não é possível falhar um pagamento já confirmado.")

        self.failed_date = datetime.now(timezone.utc)
        self.payment_status = PaymentStatus.FAILED
