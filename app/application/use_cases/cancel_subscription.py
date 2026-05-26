from datetime import datetime, timezone

from app.application.dtos.request.subscription_cancel import CancelSubscriptionDTO
from app.application.dtos.response.subscription_cancel import CancelSubscriptionResponse
from app.application.interfaces.gateway_provider import GetGateway, SubscriptionStatusResponse
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.gateway_operation_repo import GatewayOperationRepository
from app.application.repositories.subscription_repo import SubscriptionRepository
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.errors import DomainError, NotFoundError


class CancelSubscription:
    def __init__(
        self,
        get_gateway: GetGateway,
        uow: UowProvider,
        repo: SubscriptionRepository,
        gateway_operation_repo: GatewayOperationRepository,
    ):
        self.get_gateway = get_gateway
        self.uow = uow
        self.repo = repo
        self.gateway_operation_repo = gateway_operation_repo

    def _build_response(self, subscription: Subscription) -> CancelSubscriptionResponse:
        return CancelSubscriptionResponse(
            subscription_id=subscription.id,
            subscription_status=subscription.status,
            cancelled_at=subscription.cancelled_at,
        )

    def _ensure_owned_subscription(self, subscription: Subscription, system) -> None:
        if not subscription.belongs_to_system(system):
            raise NotFoundError("Assinatura nao encontrada.")

    def _operation_dedupe_key(self, request: CancelSubscriptionDTO) -> str:
        return f"cancel_subscription:{request.system.value}:{request.subscription_id}"

    def _gateway_reports_canceled(self, status_response: SubscriptionStatusResponse) -> bool:
        normalized_status = (status_response.status or "").strip().lower()
        return status_response.deleted or normalized_status in {"inactive", "canceled", "cancelled", "deleted"}

    async def _finalize_cancellation(
        self,
        request: CancelSubscriptionDTO,
        operation: GatewayOperation,
    ) -> CancelSubscriptionResponse:
        subscription = await self.repo.get_by_id_for_update(request.subscription_id)
        self._ensure_owned_subscription(subscription, request.system)

        if subscription.status != SubscriptionStatus.CANCELED:
            subscription.cancel(job_id=request.job_id, reason=request.reason)
            await self.repo.save(subscription)

        operation.mark_completed(gateway_reference=subscription.gateway_subscription_id)
        await self.gateway_operation_repo.save(operation)
        await self.uow.commit()
        return self._build_response(subscription)

    async def execute(self, request: CancelSubscriptionDTO) -> CancelSubscriptionResponse:
        subscription = await self.repo.get_by_id_for_update(request.subscription_id)
        self._ensure_owned_subscription(subscription, request.system)

        if subscription.status == SubscriptionStatus.CANCELED:
            return self._build_response(subscription)

        operation_dedupe_key = self._operation_dedupe_key(request)
        operation = await self.gateway_operation_repo.get_by_dedupe_key(operation_dedupe_key)

        if operation and operation.status == GatewayOperationStatus.COMPLETED:
            return await self._finalize_cancellation(request, operation)

        if operation and operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION:
            raise DomainError("Existe um cancelamento pendente de reconciliacao para essa assinatura.")

        if subscription.status not in {
            SubscriptionStatus.PENDING,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.CANCELLATION_PENDING,
        }:
            raise DomainError("Assinatura nao pode ser cancelada no estado atual.")

        if operation is None:
            operation = GatewayOperation(
                operation_name="cancel_subscription",
                dedupe_key=operation_dedupe_key,
                provider=subscription.gateway_provider,
                system=request.system,
                request_payload=request.model_dump(mode="json"),
            )
            operation = await self.gateway_operation_repo.save(operation)

        if subscription.status != SubscriptionStatus.CANCELLATION_PENDING:
            subscription.request_cancellation(reason=request.reason, job_id=request.job_id)
            await self.repo.save(subscription)

        await self.uow.commit()

        gateway = self.get_gateway.get(gateway=subscription.gateway_provider)

        try:
            status_response = await gateway.verify_status(subscription.gateway_subscription_id)
            if self._gateway_reports_canceled(status_response):
                return await self._finalize_cancellation(request, operation)

            gateway_reference = await gateway.cancel_subscription(subscription.gateway_subscription_id)
        except DomainError:
            raise
        except Exception as exc:
            await self.uow.rollback()
            operation.mark_failed(str(exc))
            await self.gateway_operation_repo.save(operation)
            await self.uow.commit()
            raise

        if not gateway_reference:
            gateway_reference = subscription.gateway_subscription_id

        operation.mark_completed(gateway_reference=gateway_reference)

        subscription = await self.repo.get_by_id_for_update(request.subscription_id)
        self._ensure_owned_subscription(subscription, request.system)
        if subscription.status != SubscriptionStatus.CANCELED:
            subscription.cancel(job_id=request.job_id, reason=request.reason, cancelled_at=datetime.now(timezone.utc))
            await self.repo.save(subscription)

        await self.gateway_operation_repo.save(operation)
        await self.uow.commit()
        return self._build_response(subscription)
