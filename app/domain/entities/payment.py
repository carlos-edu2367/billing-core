from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.movimentation_type import MovimentationType
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.system import System
from app.domain.errors import DomainError


class Payment:
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
        paid_date: datetime | None = None,
        canceled_date: datetime | None = None,
        refunded_date: datetime | None = None,
        failed_date: datetime | None = None,
        movimentation_type: MovimentationType = MovimentationType.DEFAULT_PAYMENT,
        checkout_link: str | None = None,
        id: UUID | None = None,
        subscription_id: UUID | None = None,
        webhook_link: str | None = None,
        due_date: date | None = None,
        external_reference: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
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
            raise DomainError("Pagamento nao pode ter valor liquido negativo.")

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
        self.due_date = due_date
        self.external_reference = external_reference.strip() if external_reference else None
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at or self.created_at

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

    @classmethod
    def create_standalone_payment(
        cls,
        *,
        description: str,
        gateway: GatewayProvider,
        system_payment_id: str,
        provider_payment_id: str,
        value: Decimal,
        from_system: System,
        checkout_link: str | None,
        webhook_link: str | None,
        due_date: date,
        external_reference: str,
    ) -> "Payment":
        return cls(
            description=description,
            gateway=gateway,
            system_payment_id=system_payment_id,
            provider_payment_id=provider_payment_id,
            value=value,
            from_system=from_system,
            checkout_link=checkout_link,
            webhook_link=webhook_link,
            due_date=due_date,
            external_reference=external_reference,
            movimentation_type=MovimentationType.DEFAULT_PAYMENT,
        )

    def belongs_to_subscription(self, subscription_id: UUID) -> bool:
        return self.subscription_id == subscription_id

    def mark_as_canceled(self):
        if self.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:
            raise DomainError("Nao e possivel cancelar esse pagamento.")
        self.payment_status = PaymentStatus.CANCELED
        self.canceled_date = datetime.now(timezone.utc)
        self.updated_at = self.canceled_date

    def mark_as_confirmed(self, payment_date: datetime | None = None, net_value: Decimal | None = None):
        if self.payment_status in {PaymentStatus.CONFIRMED, PaymentStatus.PAID}:
            return

        if self.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:
            raise DomainError("Nao e possivel confirmar esse pagamento.")

        self.payment_status = PaymentStatus.CONFIRMED
        self.paid_date = payment_date or datetime.now(timezone.utc)
        if net_value is not None:
            if net_value < 0:
                raise DomainError("Pagamento nao pode ter valor liquido negativo.")
            self.net_value = net_value
        self.updated_at = datetime.now(timezone.utc)

    def mark_as_overdue(self):
        if self.payment_status == PaymentStatus.PAID:
            raise DomainError("Nao e possivel vencer um pagamento ja recebido.")

        if self.payment_status in {PaymentStatus.CANCELED, PaymentStatus.REFUNDED, PaymentStatus.FAILED}:
            raise DomainError("Nao e possivel vencer esse pagamento.")

        self.payment_status = PaymentStatus.OVERDUE
        self.updated_at = datetime.now(timezone.utc)

    def mark_as_paid(self, payment_date: datetime | None = None, net_value: Decimal | None = None):
        if self.payment_status == PaymentStatus.PAID:
            return

        if self.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE, PaymentStatus.CONFIRMED}:
            raise DomainError("Nao e possivel confirmar esse pagamento.")

        self.payment_status = PaymentStatus.PAID
        self.paid_date = payment_date or datetime.now(timezone.utc)
        if net_value is not None:
            if net_value < 0:
                raise DomainError("Pagamento nao pode ter valor liquido negativo.")
            self.net_value = net_value
        self.updated_at = datetime.now(timezone.utc)

    def mark_as_refunded(self):
        if self.payment_status not in {PaymentStatus.PAID, PaymentStatus.CONFIRMED}:
            raise DomainError("Nao e possivel estornar um pagamento nao pago.")

        self.refunded_date = datetime.now(timezone.utc)
        self.payment_status = PaymentStatus.REFUNDED
        self.updated_at = self.refunded_date

    def mark_as_failed(self):
        if self.payment_status == PaymentStatus.PAID:
            raise DomainError("Nao e possivel falhar um pagamento ja confirmado.")

        self.failed_date = datetime.now(timezone.utc)
        self.payment_status = PaymentStatus.FAILED
        self.updated_at = self.failed_date
