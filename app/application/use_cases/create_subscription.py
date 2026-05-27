from datetime import datetime, timezone

from app.application.dtos.request.subscription import CreateSubscriptionDTO
from app.application.dtos.response.subscription import CreateSubscriptionResponse
from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.application.repositories.payment_repo import PaymentRepository
from app.application.repositories.subscription_repo import SubscriptionRepository
from app.domain.entities.customer import Customer
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.entities.subscription import Subscription, SubscriptionStatus
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.errors import DomainError
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType


class CreateSubscription:
    def __init__(
        self,
        get_gateway: GetGateway,
        uow: UowProvider,
        repo: SubscriptionRepository,
        payment_repo: PaymentRepository,
        gateway_operation_repo: GatewayOperationRepository,
    ):
        self.get_gateway = get_gateway
        self.uow = uow
        self.repo = repo
        self.payment_repo = payment_repo
        self.gateway_operation_repo = gateway_operation_repo

    async def _build_existing_response(self, subscription: Subscription) -> CreateSubscriptionResponse:
        payments = await self.payment_repo.list_by_subscription_id(subscription.id)
        payment = next((item for item in payments if item.checkout_link), payments[0] if payments else None)

        if payment is None:
            raise DomainError("Assinatura duplicada detectada sem pagamento rastreavel.")

        return CreateSubscriptionResponse(
            subscription_id=subscription.id,
            payment_id=payment.id,
            value=payment.value,
            checkout_url=payment.checkout_link,
            payment_status=payment.payment_status,
            subscription_status=subscription.status,
        )

    async def execute(self, request: CreateSubscriptionDTO, customer: Customer):
        if request.system != customer.system:
            raise DomainError("Sistema da assinatura difere do customer informado.")

        if not customer.has_provider_binding():
            raise DomainError("Customer sem vinculacao no gateway para criacao de assinatura.")

        existing_subscription = await self.repo.get_by_system_ref(request.system_sub_id, request.system)
        if existing_subscription:
            return await self._build_existing_response(existing_subscription)

        operation_dedupe_key = f"create_subscription:{request.system.value}:{request.system_sub_id}"
        existing_operation = await self.gateway_operation_repo.get_by_dedupe_key(operation_dedupe_key)
        if existing_operation:
            if existing_operation.status == GatewayOperationStatus.COMPLETED:
                raise DomainError("Existe uma operacao concluida sem espelho local consistente. Requer reconciliacao antes de nova tentativa.")
            if existing_operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION:
                raise DomainError("Existe uma operacao pendente de reconciliacao para essa assinatura.")
            raise DomainError("Ja existe uma operacao de criacao de assinatura em andamento ou falha recente para essa referencia.")

        operation = existing_operation or GatewayOperation(
            operation_name="create_subscription",
            dedupe_key=operation_dedupe_key,
            provider=customer.gateway_provider,
            system=request.system,
            request_payload=request.model_dump(mode="json"),
        )

        if not operation.id:
            operation = await self.gateway_operation_repo.save(operation)
            await self.uow.commit()

        gateway = self.get_gateway.get(gateway=customer.gateway_provider)
        gateway_subscription_id: str | None = None

        try:
            gateway_subscription_id = await gateway.create_subscription(
                customer_provider_id=customer.provider_customer_id,
                billing_type=PaymentType.CREDIT_CARD,
                value=request.value,
                next_due_date=request.next_due_date or datetime.now(timezone.utc).date(),
                cycle=request.subscription_type,
                description=request.description,
                external_reference=request.system_sub_id,
            )

            subscription = Subscription(
                initial_date=datetime.now(timezone.utc),
                description=request.description,
                system_subscription_id=request.system_sub_id,
                gateway_subscription_id=gateway_subscription_id,
                gateway_provider=customer.gateway_provider,
                status=SubscriptionStatus.PENDING,
                last_paid_date=None,
                from_system=customer.system,
                subscription_type=request.subscription_type,
                expires_at=request.expires_at,
                next_due_date=request.next_due_date,
                value=request.value,
                webhook_link=request.webhook_link,
            )
            subscription = await self.repo.save(subscription)

            payments = await gateway.get_subscription_payment(gateway_subscription_id)
            payment_info = next((item for item in payments if item.status.upper() == "PENDING"), None)
            if payment_info is None and payments:
                payment_info = payments[0]
            if payment_info is None:
                raise DomainError("Gateway nao retornou pagamento para a assinatura criada.")

            billing_type_val = getattr(payment_info, "billing_type", "CREDIT_CARD") or "CREDIT_CARD"
            try:
                p_type = PaymentType[billing_type_val.upper()]
            except KeyError:
                p_type = PaymentType.CREDIT_CARD

            payment = Payment.create_subscription_payment(
                description=f"Pagamento relacionado a assinatura: {subscription.description}",
                gateway=subscription.gateway_provider,
                system_payment_id=f"{request.system_sub_id}:{payment_info.payment_id}",
                provider_payment_id=payment_info.payment_id,
                value=payment_info.value,
                from_system=subscription.from_system,
                subscription_id=subscription.id,
                checkout_link=payment_info.invoice_url,
                payment_type=p_type,
            )

            payment = await self.payment_repo.save(payment)
            operation.mark_completed(gateway_reference=gateway_subscription_id)
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()

            return CreateSubscriptionResponse(
                subscription_id=subscription.id,
                payment_id=payment.id,
                value=payment.value,
                checkout_url=payment.checkout_link,
                payment_status=payment.payment_status,
                subscription_status=subscription.status,
            )
        except DomainError as exc:
            await self.uow.rollback()
            if gateway_subscription_id:
                operation.mark_requires_reconciliation(gateway_reference=gateway_subscription_id, error_message=str(exc))
            else:
                operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise
        except Exception as exc:
            await self.uow.rollback()
            if gateway_subscription_id:
                operation.mark_requires_reconciliation(gateway_reference=gateway_subscription_id, error_message=str(exc))
                await self.gateway_operation_repo.save(operation)
                await self.uow.commit()
                raise DomainError(
                    "Assinatura criada no gateway, mas a sincronizacao local falhou. Operacao marcada para reconciliacao."
                ) from exc

            operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise
