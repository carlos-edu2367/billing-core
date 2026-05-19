import calendar
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from app.domain.errors import DomainError
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System


def _add_billing_period(base: datetime, subscription_type: SubscriptionType) -> datetime:
    """Adds the billing period to base using calendar-accurate arithmetic.

    Clamps day to the last valid day of the target month (e.g. Jan 31 + 1 month = Feb 28/29).
    """
    if subscription_type == SubscriptionType.YEARLY:
        months_to_add = 12
    elif subscription_type == SubscriptionType.SEMIANNUAL:
        months_to_add = 6
    else:
        months_to_add = 1

    total_months = base.month - 1 + months_to_add
    target_year = base.year + total_months // 12
    target_month = total_months % 12 + 1
    target_day = min(base.day, calendar.monthrange(target_year, target_month)[1])
    return base.replace(year=target_year, month=target_month, day=target_day)


class Subscription():
    def __init__(
        self,
        initial_date: datetime,
        description: str,
        system_subscription_id: str,
        gateway_subscription_id: str,
        gateway_provider: GatewayProvider,
        status: SubscriptionStatus,
        last_paid_date: datetime | None,
        from_system: System,
        subscription_type: SubscriptionType,
        expires_at: datetime,
        trial_days: int = 0,
        value: Decimal = Decimal(0),
        trial_ends_at: datetime | None = None,
        next_due_date: datetime | None = None,
        cancelled_at: datetime | None = None,
        id: UUID = None,
        webhook_link: str | None = None,
    ):
        normalized_description = (description or "").strip()
        normalized_system_subscription_id = (system_subscription_id or "").strip()
        normalized_gateway_subscription_id = (gateway_subscription_id or "").strip()

        if not normalized_description:
            raise DomainError("Assinatura precisa ter descrição.")

        if not normalized_system_subscription_id:
            raise DomainError("Assinatura precisa ter identificador do sistema.")

        if not normalized_gateway_subscription_id:
            raise DomainError("Assinatura precisa ter identificador do gateway.")

        if value < 0:
            raise DomainError("Assinatura não pode ter valor negativo.")

        if trial_days < 0:
            raise DomainError("Assinatura não pode ter trial negativo.")

        self.initial_date = initial_date
        self.description = normalized_description
        self.system_subscription_id = normalized_system_subscription_id
        self.gateway_subscription_id = normalized_gateway_subscription_id
        self.gateway_provider = gateway_provider
        self.status = status
        self.last_paid_date = last_paid_date
        self.from_system = from_system
        self.subscription_type = subscription_type
        self.expires_at = expires_at
        self.trial_days = trial_days
        self.id = id
        self.trial_ends_at = trial_ends_at
        self.next_due_date = next_due_date
        self.cancelled_at = cancelled_at
        self.webhook_link = webhook_link
        self.value = value

    def mark_as_paid(self, payment_date: datetime):
        if payment_date is None:
            raise DomainError("Assinatura não pode ser ativada sem data de pagamento.")

        if self.status == SubscriptionStatus.CANCELED:
            raise DomainError("Assinatura cancelada não pode ser reativada por esse fluxo.")

        self.last_paid_date = payment_date
        base = max(payment_date, self.expires_at)
        self.expires_at = _add_billing_period(base, self.subscription_type)
        self.status = SubscriptionStatus.ACTIVE

    def is_in_trial(self) -> bool:
        if not self.trial_ends_at:
            return False
        return datetime.now(timezone.utc) < self.trial_ends_at

    def is_active(self) -> bool:
        return self.status == SubscriptionStatus.ACTIVE and self.expires_at > datetime.now(timezone.utc)

    def belongs_to_system(self, system: System) -> bool:
        return self.from_system == system

    def cancel(self):
        if self.status == SubscriptionStatus.CANCELED:
            return

        self.status = SubscriptionStatus.CANCELED
        self.cancelled_at = datetime.now(timezone.utc)
