from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.dtos.request.subscription_cancel import CancelSubscriptionDTO
from app.application.use_cases.cancel_subscription import CancelSubscription
from app.domain.entities.gateway_operation import GatewayOperation
from app.domain.entities.subscription import Subscription
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System


class FakeGateway:
    def __init__(self, verify_status="ACTIVE", deleted=False, fail_cancel=False):
        self.verify_status_value = verify_status
        self.verify_deleted = deleted
        self.fail_cancel = fail_cancel
        self.cancel_called = 0
        self.verify_called = 0

    async def verify_status(self, subscription_id: str):
        self.verify_called += 1
        return type(
            "StatusResponse",
            (),
            {
                "subscription_id": subscription_id,
                "status": self.verify_status_value,
                "deleted": self.verify_deleted,
                "next_due_date": datetime.now(timezone.utc).date(),
                "value": Decimal("99.90"),
                "cycle": "MONTHLY",
            },
        )()

    async def cancel_subscription(self, subscription_id: str):
        self.cancel_called += 1
        if self.fail_cancel:
            raise RuntimeError("gateway timeout")
        return subscription_id


class FakeGetGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    def get(self, gateway):
        return self.gateway


class FakeSubscriptionRepo:
    def __init__(self, subscription: Subscription):
        self.subscription = subscription
        self.saved: list[Subscription] = []

    async def get_by_id_for_update(self, subscription_id):
        return self.subscription

    async def save(self, subscription: Subscription):
        self.subscription = subscription
        self.saved.append(subscription)
        return subscription


class FakeGatewayOperationRepo:
    def __init__(self, existing: GatewayOperation | None = None):
        self.existing = existing
        self.saved: list[GatewayOperation] = []

    async def get_by_dedupe_key(self, dedupe_key: str):
        return self.existing

    async def save(self, operation: GatewayOperation):
        if operation.id is None:
            operation.id = uuid4()
        self.existing = operation
        self.saved.append(operation)
        return operation


class FakeUow:
    def __init__(self):
        self.commit_called = 0
        self.rollback_called = 0

    async def commit(self):
        self.commit_called += 1

    async def rollback(self):
        self.rollback_called += 1


def make_subscription(status=SubscriptionStatus.ACTIVE):
    return Subscription(
        initial_date=datetime.now(timezone.utc),
        description="Plano Pro",
        system_subscription_id="sub-1",
        gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS,
        status=status,
        last_paid_date=None,
        from_system=System.NEECTIFY_SHOP,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc),
        id=uuid4(),
        value=Decimal("99.90"),
    )


def make_request(subscription_id):
    return CancelSubscriptionDTO(
        subscription_id=subscription_id,
        system=System.NEECTIFY_SHOP,
        reason="pedido do cliente",
        job_id="job-1",
    )


@pytest.mark.asyncio
async def test_cancel_subscription_marks_local_subscription_as_canceled():
    subscription = make_subscription()
    gateway = FakeGateway()
    repo = FakeSubscriptionRepo(subscription)
    operation_repo = FakeGatewayOperationRepo()
    uow = FakeUow()
    service = CancelSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=uow,
        repo=repo,
        gateway_operation_repo=operation_repo,
    )

    response = await service.execute(make_request(subscription.id))

    assert response.subscription_id == subscription.id
    assert response.subscription_status == SubscriptionStatus.CANCELED
    assert repo.subscription.status == SubscriptionStatus.CANCELED
    assert repo.subscription.cancellation_reason == "pedido do cliente"
    assert gateway.cancel_called == 1
    assert operation_repo.saved[-1].status == GatewayOperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancel_subscription_uses_gateway_status_to_avoid_duplicate_cancel():
    subscription = make_subscription(status=SubscriptionStatus.CANCELLATION_PENDING)
    gateway = FakeGateway(verify_status="INACTIVE")
    repo = FakeSubscriptionRepo(subscription)
    operation_repo = FakeGatewayOperationRepo(
        existing=GatewayOperation(
            operation_name="cancel_subscription",
            dedupe_key=f"cancel_subscription:{System.NEECTIFY_SHOP.value}:{subscription.id}",
            provider=GatewayProvider.ASAAS,
            system=System.NEECTIFY_SHOP,
            request_payload={},
            status=GatewayOperationStatus.FAILED,
        )
    )
    service = CancelSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=FakeUow(),
        repo=repo,
        gateway_operation_repo=operation_repo,
    )

    response = await service.execute(make_request(subscription.id))

    assert response.subscription_status == SubscriptionStatus.CANCELED
    assert gateway.cancel_called == 0
    assert gateway.verify_called == 1


@pytest.mark.asyncio
async def test_cancel_subscription_keeps_pending_state_when_gateway_fails_temporarily():
    subscription = make_subscription()
    gateway = FakeGateway(fail_cancel=True)
    repo = FakeSubscriptionRepo(subscription)
    operation_repo = FakeGatewayOperationRepo()
    uow = FakeUow()
    service = CancelSubscription(
        get_gateway=FakeGetGateway(gateway),
        uow=uow,
        repo=repo,
        gateway_operation_repo=operation_repo,
    )

    with pytest.raises(RuntimeError):
        await service.execute(make_request(subscription.id))

    assert repo.subscription.status == SubscriptionStatus.CANCELLATION_PENDING
    assert operation_repo.saved[-1].status == GatewayOperationStatus.FAILED
    assert gateway.cancel_called == 1
