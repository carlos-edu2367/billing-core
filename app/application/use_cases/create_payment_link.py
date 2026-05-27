from app.application.dtos.request.payment_link import CreatePaymentLinkDTO
from app.application.dtos.response.payment_link import CreatePaymentLinkResponse
from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.errors import DomainError


class CreatePaymentLink:
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

    async def execute(
        self,
        request: CreatePaymentLinkDTO,
        gateway_provider: GatewayProvider,
    ) -> CreatePaymentLinkResponse:
        existing_payment = await self.payment_repo.get_by_system_ref(request.system_payment_id, request.system)
        if existing_payment:
            return CreatePaymentLinkResponse(
                payment_id=existing_payment.id,
                checkout_url=existing_payment.checkout_link,
                payment_status=existing_payment.payment_status,
            )

        external_reference = f"payment:{request.system.value}:{request.system_payment_id}"
        operation_dedupe_key = f"create_payment_link:{request.system.value}:{request.system_payment_id}"
        existing_operation = await self.gateway_operation_repo.get_by_dedupe_key(operation_dedupe_key)
        operation = existing_operation
        if existing_operation:
            if existing_operation.status == GatewayOperationStatus.COMPLETED:
                raise DomainError("Existe uma operacao concluida sem espelho local consistente. Requer reconciliacao antes de nova tentativa.")
            if existing_operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION:
                raise DomainError("Existe uma operacao pendente de reconciliacao para esse payment link.")
            if existing_operation.status != GatewayOperationStatus.FAILED:
                raise DomainError("Ja existe uma operacao de criacao de payment link em andamento para essa referencia.")

        if operation is None:
            operation = GatewayOperation(
                operation_name="create_payment_link",
                dedupe_key=operation_dedupe_key,
                provider=gateway_provider,
                system=request.system,
                request_payload=request.model_dump(mode="json"),
            )
            operation = await self.gateway_operation_repo.save(operation)
            await self.uow.commit()

        gateway = self.get_gateway.get(gateway=gateway_provider)
        gateway_payment_link_id = None

        try:
            link_info = await gateway.create_payment_link(
                name=request.description,
                value=request.value,
                billing_type=request.billing_type,
                description=request.description,
                external_reference=external_reference,
                due_date_limit_days=request.due_date_limit_days,
            )
            gateway_payment_link_id = link_info.payment_link_id
            payment = Payment.create_standalone_payment(
                description=request.description,
                gateway=gateway_provider,
                system_payment_id=request.system_payment_id,
                provider_payment_id=link_info.payment_link_id,
                value=request.value,
                from_system=request.system,
                checkout_link=link_info.checkout_url,
                webhook_link=request.webhook_link,
                due_date=None,
                external_reference=external_reference,
            )
            payment.payment_status = PaymentStatus.PENDING
            payment.payment_type = request.billing_type
            payment = await self.payment_repo.save(payment)
            operation.mark_completed(gateway_reference=gateway_payment_link_id)
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            return CreatePaymentLinkResponse(
                payment_id=payment.id,
                checkout_url=payment.checkout_link,
                payment_status=payment.payment_status,
            )
        except Exception as exc:
            await self.uow.rollback()
            if gateway_payment_link_id:
                operation.mark_requires_reconciliation(gateway_reference=gateway_payment_link_id, error_message=str(exc))
                await self.gateway_operation_repo.save(operation)
                await self.uow.commit()
                raise DomainError(
                    "Payment link criado no gateway, mas a sincronizacao local falhou. Operacao marcada para reconciliacao."
                ) from exc

            operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise
