import pytest
from datetime import datetime, timezone, date
from uuid import uuid4
from decimal import Decimal
from types import SimpleNamespace

from app.workers import tasks
from app.domain.enums.subscription_status import SubscriptionStatus
from app.domain.enums.system import System
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.enums.payment_status import PaymentStatus
from app.domain.errors import NotFoundError
from app.domain.entities.gateway_operation import GatewayOperation
from app.application.interfaces.gateway_provider import CreateCheckoutGatewayResponse


class DummySession:
    def __init__(self, op_record=None):
        self.op_record = op_record
        self.rolled_back = 0
        self.committed = 0
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return self

    async def get(self, model, id, with_for_update=False):
        if self.op_record and self.op_record.id == id:
            return self.op_record
        return None

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass


class FakeGateway:
    def __init__(self, verify_resp=None, payments=None):
        self.verify_resp = verify_resp or SimpleNamespace(
            subscription_id="gw-sub-123",
            status="ACTIVE",
            deleted=False,
            next_due_date=date.today(),
            value=Decimal("99.90"),
            cycle="MONTHLY"
        )
        self.payments = payments or []

    async def verify_status(self, subscription_id):
        return self.verify_resp

    async def get_subscription_payment(self, subscription_id):
        return self.payments


class FakeGetGateway:
    def __init__(self, gateway):
        self._gateway = gateway

    def get(self, provider):
        return self._gateway


class FakeOpRepo:
    def __init__(self, operations=None):
        self.operations = operations or []
        self.saved = []

    async def list_by_status(self, status):
        return self.operations

    async def save(self, op):
        self.saved.append(op)
        return op


class FakeSubRepo:
    def __init__(self, existing_sub=None):
        self.existing_sub = existing_sub
        self.saved = []

    async def get_by_provider_id_for_update(self, provider_id):
        if self.existing_sub and self.existing_sub.gateway_subscription_id == provider_id:
            return self.existing_sub
        raise NotFoundError("Not Found")

    async def save(self, sub):
        self.saved.append(sub)
        return sub


class FakePaymentRepo:
    async def get_by_provider_id(self, id):
        raise NotFoundError("Not Found")

    async def save(self, pay):
        return pay


class CheckoutOperationRecord:
    def __init__(self, operation: GatewayOperation):
        self.id = operation.id
        self.status = operation.status.value
        self.operation = operation

    def to_domain(self):
        return self.operation


class CheckoutOperationRepo:
    def __init__(self, operation: GatewayOperation, record: CheckoutOperationRecord):
        self.operation = operation
        self.record = record
        self.saved = []

    async def list_by_status(self, status):
        return [self.operation]

    async def save(self, operation):
        self.saved.append(operation)
        self.record.status = operation.status.value
        return operation


class CheckoutPaymentRepo:
    def __init__(self):
        self.existing = None
        self.saved = []

    async def get_by_system_ref(self, system_id, system):
        if self.existing and self.existing.system_payment_id == system_id and self.existing.from_system == system:
            return self.existing
        return None

    async def save(self, payment):
        if payment.id is None:
            payment.id = uuid4()
        self.existing = payment
        self.saved.append(payment)
        return payment


class CheckoutDeliveryRepo:
    def __init__(self):
        self.by_dedupe_key = {}
        self.saved = []

    async def get_by_dedupe_key(self, dedupe_key):
        return self.by_dedupe_key.get(dedupe_key)

    async def save(self, delivery):
        if delivery.id is None:
            delivery.id = uuid4()
        self.by_dedupe_key[delivery.dedupe_key] = delivery
        self.saved.append(delivery)
        return delivery


class CheckoutGateway:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.get_checkout_called = 0

    async def get_checkout(self, checkout_id):
        self.get_checkout_called += 1
        if self.error:
            raise self.error
        return self.response


def make_checkout_operation() -> GatewayOperation:
    operation = GatewayOperation(
        operation_name="create_checkout",
        dedupe_key="create_checkout:marketfy:order-123",
        provider=GatewayProvider.ASAAS,
        system=System.MARKETFY,
        request_payload={
            "system_payment_id": "order-123",
            "description": "Pedido 123",
            "value": 72.0,
            "webhook_link": "https://hooks.neectify.local/billing/payment",
        },
        gateway_reference="checkout_123",
        status=GatewayOperationStatus.REQUIRES_RECONCILIATION,
    )
    operation.id = uuid4()
    return operation


def checkout_response(status="ACTIVE", external_reference="checkout:marketfy:order-123"):
    return CreateCheckoutGatewayResponse(
        checkout_id="checkout_123",
        checkout_url="https://sandbox.asaas.com/checkoutSession/show/checkout_123",
        status=status,
        external_reference=external_reference,
    )


def configure_checkout_reconciliation(monkeypatch, operation, gateway):
    record = CheckoutOperationRecord(operation)
    session = DummySession(record)
    operation_repo = CheckoutOperationRepo(operation, record)
    payment_repo = CheckoutPaymentRepo()
    delivery_repo = CheckoutDeliveryRepo()

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda s: operation_repo)
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda s: payment_repo)
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", lambda s: delivery_repo)
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda s: FakeSubRepo())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: FakeGetGateway(gateway))
    return operation_repo, payment_repo, delivery_repo


@pytest.mark.asyncio
async def test_reconcile_worker_skips_when_lock_held(monkeypatch, fake_redis):
    # Set the lock key beforehand
    await fake_redis.set("billing_core:reconcile_worker_lock", "locked")

    ctx = {
        "redis": fake_redis,
        "logger": SimpleNamespace(
            info=lambda msg, *a, **k: None,
            error=lambda msg, *a, **k: None
        )
    }

    result = await tasks.reconcile_gateway_operations_worker(ctx)
    assert result["status"] == "skipped"
    assert result["reason"] == "lock_held"


@pytest.mark.asyncio
async def test_reconcile_worker_converts_jsonb_value_to_decimal(monkeypatch, fake_redis):
    op_payload = {
        "system_sub_id": "sys-sub-456",
        "description": "Plano Premium",
        "subscription_type": "MONTHLY",
        "expires_at": datetime.now(timezone.utc).isoformat(),
        "next_due_date": datetime.now(timezone.utc).isoformat(),
        "value": 149.90,  # float value in JSONB
        "webhook_link": "https://hook.neectify.local"
    }

    op_id = uuid4()
    op_model = SimpleNamespace(
        id=op_id,
        operation_name="create_subscription",
        dedupe_key="create_subscription:marketfy:sys-sub-456",
        provider="asaas",
        system="marketfy",
        request_payload=op_payload,
        status="requires_reconciliation",
        gateway_reference="gw-sub-456",
        error_message="sync error",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        to_domain=lambda: SimpleNamespace(
            id=op_id,
            operation_name="create_subscription",
            dedupe_key="create_subscription:marketfy:sys-sub-456",
            provider=GatewayProvider.ASAAS,
            system=System.MARKETFY,
            request_payload=op_payload,
            status=GatewayOperationStatus.REQUIRES_RECONCILIATION,
            gateway_reference="gw-sub-456",
            created_at=datetime.now(timezone.utc),
            mark_completed=lambda gateway_reference: None
        )
    )

    dummy_session = DummySession(op_model)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: dummy_session)
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda s: FakeOpRepo([op_model.to_domain()]))

    # Gateways and Repos
    gateway = FakeGateway()
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: FakeGetGateway(gateway))
    
    sub_repo = FakeSubRepo()
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda s: sub_repo)
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda s: FakePaymentRepo())
    monkeypatch.setattr(tasks, "UowProvider", lambda s: SimpleNamespace())

    ctx = {
        "redis": fake_redis,
        "logger": SimpleNamespace(
            info=lambda msg, *a, **k: None,
            error=lambda msg, *a, **k: None
        )
    }

    await tasks.reconcile_gateway_operations_worker(ctx)

    # Verify subscription was created with Decimal value
    assert len(sub_repo.saved) == 1
    assert sub_repo.saved[0].value == Decimal("149.9")
    assert isinstance(sub_repo.saved[0].value, Decimal)


@pytest.mark.asyncio
async def test_reconcile_worker_handles_deleted_subscription(monkeypatch, fake_redis):
    op_payload = {
        "system_sub_id": "sys-sub-456",
        "description": "Plano Premium",
        "subscription_type": "MONTHLY",
        "expires_at": datetime.now(timezone.utc).isoformat(),
        "next_due_date": datetime.now(timezone.utc).isoformat(),
        "value": 149.90,
        "webhook_link": "https://hook.neectify.local"
    }

    op_id = uuid4()
    op_model = SimpleNamespace(
        id=op_id,
        operation_name="create_subscription",
        dedupe_key="create_subscription:marketfy:sys-sub-456",
        provider="asaas",
        system="marketfy",
        request_payload=op_payload,
        status="requires_reconciliation",
        gateway_reference="gw-sub-456",
        error_message="sync error",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        to_domain=lambda: SimpleNamespace(
            id=op_id,
            operation_name="create_subscription",
            dedupe_key="create_subscription:marketfy:sys-sub-456",
            provider=GatewayProvider.ASAAS,
            system=System.MARKETFY,
            request_payload=op_payload,
            status=GatewayOperationStatus.REQUIRES_RECONCILIATION,
            gateway_reference="gw-sub-456",
            created_at=datetime.now(timezone.utc),
            mark_completed=lambda gateway_reference: None
        )
    )

    dummy_session = DummySession(op_model)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: dummy_session)
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda s: FakeOpRepo([op_model.to_domain()]))

    # Gateway returns deleted status
    gateway = FakeGateway(verify_resp=SimpleNamespace(
        subscription_id="gw-sub-456",
        status="DELETED",
        deleted=True,
        next_due_date=date.today(),
        value=Decimal("149.90"),
        cycle="MONTHLY"
    ))
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: FakeGetGateway(gateway))
    
    sub_repo = FakeSubRepo()
    monkeypatch.setattr(tasks, "SubscriptionRepositoryINFRA", lambda s: sub_repo)
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda s: FakePaymentRepo())
    monkeypatch.setattr(tasks, "UowProvider", lambda s: SimpleNamespace())

    ctx = {
        "redis": fake_redis,
        "logger": SimpleNamespace(
            info=lambda msg, *a, **k: None,
            error=lambda msg, *a, **k: None
        )
    }

    await tasks.reconcile_gateway_operations_worker(ctx)

    # Subscription should be created as CANCELED locally since it is deleted in the gateway
    assert len(sub_repo.saved) == 1
    assert sub_repo.saved[0].status == SubscriptionStatus.CANCELED


@pytest.mark.asyncio
async def test_reconcile_worker_recovers_active_checkout(monkeypatch, fake_redis):
    operation = make_checkout_operation()
    gateway = CheckoutGateway(checkout_response("ACTIVE"))
    operation_repo, payment_repo, delivery_repo = configure_checkout_reconciliation(monkeypatch, operation, gateway)
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    await tasks.reconcile_gateway_operations_worker(ctx)

    assert payment_repo.saved[0].provider_payment_id == "checkout_123"
    assert payment_repo.saved[0].payment_status == PaymentStatus.PENDING
    assert payment_repo.saved[0].due_date is None
    assert operation.status == GatewayOperationStatus.COMPLETED
    assert delivery_repo.saved == []


@pytest.mark.asyncio
async def test_reconcile_worker_recovers_paid_checkout_and_enqueues_deduplicated_delivery(monkeypatch, fake_redis):
    operation = make_checkout_operation()
    gateway = CheckoutGateway(checkout_response("PAID"))
    operation_repo, payment_repo, delivery_repo = configure_checkout_reconciliation(monkeypatch, operation, gateway)
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    await tasks.reconcile_gateway_operations_worker(ctx)

    assert payment_repo.saved[0].payment_status == PaymentStatus.PAID
    assert payment_repo.saved[0].paid_date is not None
    assert payment_repo.saved[0].paid_date.tzinfo is not None
    assert operation.status == GatewayOperationStatus.COMPLETED
    assert len(delivery_repo.saved) == 1
    assert len(fake_redis.enqueued_jobs) == 1
    assert fake_redis.enqueued_jobs[0][0][0] == "workers:tasks.send_internal_webhook"


@pytest.mark.asyncio
async def test_reconcile_worker_checkout_rerun_is_idempotent(monkeypatch, fake_redis):
    operation = make_checkout_operation()
    gateway = CheckoutGateway(checkout_response("ACTIVE"))
    operation_repo, payment_repo, delivery_repo = configure_checkout_reconciliation(monkeypatch, operation, gateway)
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    await tasks.reconcile_gateway_operations_worker(ctx)
    await tasks.reconcile_gateway_operations_worker(ctx)

    assert len(payment_repo.saved) == 1
    assert gateway.get_checkout_called == 1
    assert len(delivery_repo.saved) == 0


@pytest.mark.asyncio
async def test_reconcile_worker_keeps_checkout_for_reconciliation_when_reference_mismatches(monkeypatch, fake_redis):
    operation = make_checkout_operation()
    gateway = CheckoutGateway(checkout_response("ACTIVE", "checkout:marketfy:another-order"))
    operation_repo, payment_repo, delivery_repo = configure_checkout_reconciliation(monkeypatch, operation, gateway)
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    await tasks.reconcile_gateway_operations_worker(ctx)

    assert operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION
    assert payment_repo.saved == []
    assert delivery_repo.saved == []


@pytest.mark.asyncio
async def test_reconcile_worker_keeps_checkout_for_reconciliation_when_gateway_lookup_fails(monkeypatch, fake_redis):
    operation = make_checkout_operation()
    gateway = CheckoutGateway(error=RuntimeError("gateway unavailable"))
    operation_repo, payment_repo, delivery_repo = configure_checkout_reconciliation(monkeypatch, operation, gateway)
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    await tasks.reconcile_gateway_operations_worker(ctx)

    assert operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION
    assert payment_repo.saved == []
    assert delivery_repo.saved == []


@pytest.mark.asyncio
async def test_reconcile_worker_leaves_checkout_without_gateway_reference_for_manual_reconciliation(monkeypatch, fake_redis):
    operation = make_checkout_operation()
    operation.gateway_reference = None
    gateway = CheckoutGateway(checkout_response("ACTIVE"))
    operation_repo, payment_repo, delivery_repo = configure_checkout_reconciliation(monkeypatch, operation, gateway)
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None)}

    await tasks.reconcile_gateway_operations_worker(ctx)

    assert gateway.get_checkout_called == 0
    assert operation.status == GatewayOperationStatus.REQUIRES_RECONCILIATION
    assert operation_repo.saved == []
    assert payment_repo.saved == []
    assert delivery_repo.saved == []
