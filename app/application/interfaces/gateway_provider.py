from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.application.dtos.request.webhook import WebhookPayload
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_type import SubscriptionType


@dataclass
class SubscriptionPaymentResponse:
    payment_id: str
    status: str
    due_date: date
    value: Decimal
    invoice_url: str | None
    billing_type: str


@dataclass
class SubscriptionStatusResponse:
    subscription_id: str
    status: str
    deleted: bool
    next_due_date: date
    value: Decimal
    cycle: str


@dataclass
class CreatePaymentGatewayResponse:
    payment_id: str
    status: str
    value: Decimal
    due_date: date
    invoice_url: str | None
    billing_type: str
    external_reference: str | None


@dataclass
class PaymentStatusGatewayResponse:
    payment_id: str
    status: str
    value: Decimal
    net_value: Decimal | None
    due_date: date | None
    payment_date: date | None
    invoice_url: str | None
    billing_type: str
    external_reference: str | None


@dataclass
class GetCustomerResponse:
    cus_id: str
    name: str
    email: str
    external_reference: str | None
    deleted: bool


class InterfaceGateway(ABC):
    @abstractmethod
    def normalize_webhook(self, payload: dict) -> WebhookPayload:
        pass

    @abstractmethod
    async def create_subscription(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        next_due_date: date,
        cycle: SubscriptionType,
        description: str,
    ) -> str:
        pass

    @abstractmethod
    async def get_subscription_payment(self, subscription_id: str) -> list[SubscriptionPaymentResponse]:
        pass

    @abstractmethod
    async def cancel_subscription(self, subscription_id: str) -> str:
        pass

    @abstractmethod
    async def verify_status(self, subscription_id: str) -> SubscriptionStatusResponse:
        pass

    @abstractmethod
    async def create_payment(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        due_date: date,
        description: str,
        external_reference: str,
    ) -> CreatePaymentGatewayResponse:
        pass

    @abstractmethod
    async def get_payment(self, payment_id: str) -> PaymentStatusGatewayResponse:
        pass

    @abstractmethod
    async def create_customer(
        self,
        name: str,
        cpfCnpj: str,
        email: str,
        external_reference: str,
    ) -> GetCustomerResponse:
        pass

    @abstractmethod
    async def get_customer(self, cus_id: str) -> GetCustomerResponse | None:
        pass


class GetGateway(ABC):
    @abstractmethod
    def get(self, gateway: GatewayProvider) -> InterfaceGateway:
        pass
