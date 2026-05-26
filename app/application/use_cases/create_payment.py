from app.application.dtos.request.payment import CreatePaymentDTO
from app.application.dtos.response.payment import CreatePaymentResponse
from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.customer import Customer
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.payment_status import PaymentStatus
from app.domain.errors import DomainError


class CreatePayment:
    def __init__(
        self,
        get_gateway: GetGateway,
        uow: UowProvider,
        payment_repo: PaymentRepository,
        gateway_operation_repo: GatewayOperationRepository,
    ):
        self.get_gateway = get_gateway
        self.uow = uow
        self.payment_repo = payment_repo
        self.gateway_operation_repo = gateway_operation_repo

    async def execute(self, request: CreatePaymentDTO, customer: Customer) -> CreatePaymentResponse:
        if request.system != customer.system:
            raise DomainError("Sistema do pagamento difere do customer informado.")

        if not customer.has_provider_binding():
            raise DomainError("Customer sem vinculacao no gateway para criacao de pagamento.")

        existing_payment = await self.payment_repo.get_by_system_ref(request.system_payment_id, request.system)
        if existing_payment:
            return CreatePaymentResponse(
                payment_id=existing_payment.id,
                value=existing_payment.value,
                checkout_url=existing_payment.checkout_link,
                payment_status=existing_payment.payment_status,
                billing_type=existing_payment.payment_type,
                due_date=existing_payment.due_date,
            )

        external_reference = f"payment:{request.system.value}:{request.system_payment_id}"
        operation_dedupe_key = f"create_payment:{request.system.value}:{request.system_payment_id}"
        existing_operation = await self.gateway_operation_repo.get_by_dedupe_key(operation_dedupe_key)
        operation = existing_operation
        if existing_operation:
            if existing_operation.status == GatewayOperationStatus.COMPLETED:
                raise DomainError("Existe uma operacao concluida sem espelho local consistente. Requer reconciliacao antes de nova tentativa.")
            if existing_operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION:
                raise DomainError("Existe uma operacao pendente de reconciliacao para esse pagamento.")
            if existing_operation.status != GatewayOperationStatus.FAILED:
                raise DomainError("Ja existe uma operacao de criacao de pagamento em andamento para essa referencia.")

        if operation is None:
            operation = GatewayOperation(
                operation_name="create_payment",
                dedupe_key=operation_dedupe_key,
                provider=customer.gateway_provider,
                system=request.system,
                request_payload=request.model_dump(mode="json"),
            )
            operation = await self.gateway_operation_repo.save(operation)
            await self.uow.commit()

        gateway = self.get_gateway.get(gateway=customer.gateway_provider)
        gateway_payment_id = None

        try:
            payment_info = await gateway.create_payment(
                customer_provider_id=customer.provider_customer_id,
                billing_type=request.billing_type,
                value=request.value,
                due_date=request.due_date,
                description=request.description,
                external_reference=external_reference,
            )
            gateway_payment_id = payment_info.payment_id
            payment = Payment.create_standalone_payment(
                description=request.description,
                gateway=customer.gateway_provider,
                system_payment_id=request.system_payment_id,
                provider_payment_id=payment_info.payment_id,
                value=payment_info.value,
                from_system=request.system,
                checkout_link=payment_info.invoice_url,
                webhook_link=request.webhook_link,
                due_date=payment_info.due_date,
                external_reference=external_reference,
            )
            payment.payment_status = PaymentStatus.PENDING
            payment.payment_type = request.billing_type
            payment = await self.payment_repo.save(payment)
            operation.mark_completed(gateway_reference=gateway_payment_id)
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            return CreatePaymentResponse(
                payment_id=payment.id,
                value=payment.value,
                checkout_url=payment.checkout_link,
                payment_status=payment.payment_status,
                billing_type=payment.payment_type,
                due_date=payment.due_date,
            )
        except Exception as exc:
            await self.uow.rollback()
            if gateway_payment_id:
                operation.mark_requires_reconciliation(gateway_reference=gateway_payment_id, error_message=str(exc))
                await self.gateway_operation_repo.save(operation)
                await self.uow.commit()
                raise DomainError(
                    "Pagamento criado no gateway, mas a sincronizacao local falhou. Operacao marcada para reconciliacao."
                ) from exc

            operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise
