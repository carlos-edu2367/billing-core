import logging

import httpx

from app.application.dtos.request.checkout import CreateCheckoutDTO
from app.application.dtos.response.checkout import CreateCheckoutResponse
from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.payment_type import PaymentType
from app.domain.errors import DomainError

logger = logging.getLogger(__name__)


def _has_uncertain_gateway_outcome(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return isinstance(exc, (httpx.RequestError, TimeoutError)) or (
        isinstance(status_code, int) and status_code >= 500
    )


class CreateCheckout:
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
        request: CreateCheckoutDTO,
        gateway_provider: GatewayProvider,
    ) -> CreateCheckoutResponse:
        existing_payment = await self.payment_repo.get_by_system_ref(request.system_payment_id, request.system)
        if existing_payment:
            return CreateCheckoutResponse(
                payment_id=existing_payment.id,
                checkout_url=existing_payment.checkout_link,
                payment_status=existing_payment.payment_status,
            )

        external_reference = f"checkout:{request.system.value}:{request.system_payment_id}"
        operation_dedupe_key = f"create_checkout:{request.system.value}:{request.system_payment_id}"
        existing_operation = await self.gateway_operation_repo.get_by_dedupe_key(operation_dedupe_key)
        operation = existing_operation
        if existing_operation:
            if existing_operation.status == GatewayOperationStatus.COMPLETED:
                raise DomainError("Existe uma operacao concluida sem espelho local consistente. Requer reconciliacao antes de nova tentativa.")
            if existing_operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION:
                raise DomainError("Existe uma operacao pendente de reconciliacao para esse checkout.")
            if existing_operation.status == GatewayOperationStatus.FAILED and existing_operation.gateway_reference:
                raise DomainError("Existe uma operacao falha com checkout remoto criado. Requer reconciliacao antes de nova tentativa.")
            if existing_operation.status != GatewayOperationStatus.FAILED:
                raise DomainError("Ja existe uma operacao de criacao de checkout em andamento para essa referencia.")

        if operation is None:
            request_payload = request.model_dump(mode="json")
            request_payload["external_reference"] = external_reference
            operation = GatewayOperation(
                operation_name="create_checkout",
                dedupe_key=operation_dedupe_key,
                provider=gateway_provider,
                system=request.system,
                request_payload=request_payload,
            )
            operation = await self.gateway_operation_repo.save(operation)
            await self.uow.commit()

        gateway_checkout_id = None

        try:
            gateway = self.get_gateway.get(gateway=gateway_provider)
            checkout_info = await gateway.create_checkout(
                billing_types=["PIX", "CREDIT_CARD"],
                charge_types=["DETACHED"],
                minutes_to_expire=request.minutes_to_expire,
                external_reference=external_reference,
                callback={
                    "successUrl": request.success_url,
                    "cancelUrl": request.cancel_url,
                    "expiredUrl": request.expired_url,
                },
                items=[
                    {
                        "externalReference": item.external_reference,
                        "name": item.name,
                        "description": item.description,
                        "quantity": item.quantity,
                        "value": float(item.value),
                    }
                    for item in request.items
                ],
            )
            gateway_checkout_id = checkout_info.checkout_id
            payment = Payment.create_standalone_payment(
                description=request.description,
                gateway=gateway_provider,
                system_payment_id=request.system_payment_id,
                provider_payment_id=checkout_info.checkout_id,
                value=request.value,
                from_system=request.system,
                checkout_link=checkout_info.checkout_url,
                webhook_link=request.webhook_link,
                due_date=None,
                external_reference=external_reference,
            )
            payment.payment_type = PaymentType.UNDEFINED
            payment.payment_status = PaymentStatus.PENDING
            payment = await self.payment_repo.save(payment)
            operation.mark_completed(gateway_reference=gateway_checkout_id)
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            return CreateCheckoutResponse(
                payment_id=payment.id,
                checkout_url=payment.checkout_link,
                payment_status=payment.payment_status,
            )
        except Exception as exc:
            await self.uow.rollback()
            if gateway_checkout_id:
                operation.mark_requires_reconciliation(gateway_reference=gateway_checkout_id, error_message=str(exc))
                await self.gateway_operation_repo.save(operation)
                await self.uow.commit()
                raise DomainError(
                    "Checkout criado no gateway, mas a sincronizacao local falhou. Operacao marcada para reconciliacao."
                ) from exc

            if _has_uncertain_gateway_outcome(exc):
                operation.mark_requires_reconciliation(gateway_reference=None, error_message=str(exc))
                await self.gateway_operation_repo.save(operation)
                await self.uow.commit()
                logger.warning(
                    "checkout_creation_outcome_uncertain",
                    extra={
                        "dedupe_key": operation.dedupe_key,
                        "external_reference": external_reference,
                        "error": str(exc),
                    },
                )
                raise DomainError(
                    "Resultado incerto ao criar checkout no gateway. Operacao marcada para reconciliacao manual."
                ) from exc

            operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise
