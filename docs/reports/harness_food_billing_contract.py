"""Cross-service contract probes: Neectify Food <-> Billing Core (subscriptions).

Loads BOTH codebases in one process and drives the real production classes.
Each test forces a specific failure mode of the integration.
"""
import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

# Este arquivo vive fora de `testpaths` (pytest.ini aponta so para `tests`), entao
# nao entra no CI. Ele precisa dos dois repositorios em disco: por padrao assume
# que sao irmaos (<raiz>/billing e "<raiz>/Neectify Food"). Se o seu layout for
# outro, exporte NEECTIFY_BILLING_PATH e NEECTIFY_FOOD_PATH.
_HERE = Path(__file__).resolve()
BILLING = os.environ.get("NEECTIFY_BILLING_PATH") or str(_HERE.parents[2])
FOOD = os.environ.get("NEECTIFY_FOOD_PATH") or str(
    Path(BILLING).parent / "Neectify Food" / "backend"
)

if not (Path(FOOD) / "src").is_dir():
    raise RuntimeError(
        f"Codebase do Neectify Food nao encontrado em {FOOD!r}. "
        "Defina NEECTIFY_FOOD_PATH apontando para o diretorio backend/."
    )

for p in (BILLING, FOOD):
    if p not in sys.path:
        sys.path.insert(0, p)

# Ambos os settings sao carregados no import; espelha o conftest de cada repo.
os.environ["DEBUG"] = "true"
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-bytes")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-api-key")
os.environ.setdefault("MP_WEBHOOK_SECRET", "test-mp-webhook-secret")
os.environ.setdefault("BILLING_WEBHOOK_SECRET", "test-billing-webhook-secret")
os.environ.setdefault("ASAAS_WEBHOOK_SECRET", "fake-asaas-webhook-secret-long-enough-32-chars")
os.environ.setdefault("INTERNAL_WEBHOOK_SIGNATURE", "test-webhook-signature-for-dev-only-32-chars")

# ── Billing Core (producer) ───────────────────────────────────────────────────
from app.application.dtos.request.webhook import Details, EventType, WebhookPayload
from app.application.dtos.response.webhook import (
    InternalEventType,
    SendInternalWebhookSubscription,
)
from app.application.use_cases.process_webhook import ProcessWebhookService
from app.domain.entities.subscription import Subscription as BillingSubscription
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.subscription_status import SubscriptionStatus as BillingSubStatus
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.system import System
from app.web.schemas.subscription import CreateSubscriptionRequest

# ── Neectify Food (consumer) ──────────────────────────────────────────────────
from src.application.subscription.dto import BillingWebhookPayload, CreateSubscriptionInput
from src.presentation.api.v1.subscription import build_billing_event_key
from src.application.subscription.use_cases import (
    CreateSubscriptionUseCase,
    HandleBillingWebhookUseCase,
)
from src.domain.store.entity import SubscriptionPlan
from src.domain.subscription.entity import StoreSubscription, SubscriptionStatus

WEBHOOK_SECRET = "shared-secret-for-test"


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers reproducing the real wire format
# ═══════════════════════════════════════════════════════════════════════════════

def billing_sign_and_serialize(payload_dict: dict) -> tuple[bytes, str]:
    """Exactly what InternalWebhookProvider.send does.

    It signs json.dumps(sort_keys=True, separators=(',',':')) but transmits
    httpx's own serialization of the same dict. Returns (raw_body, signature).
    """
    payload_json = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
    sig = base64.b64encode(
        hmac.new(WEBHOOK_SECRET.encode(), payload_json.encode(), hashlib.sha256).digest()
    ).decode()
    # Real bytes httpx puts on the wire for `json=payload_dict`
    raw = httpx.Request("POST", "https://x/y", json=payload_dict).read()
    return raw, sig


def food_verify(raw_body: bytes, signature: str) -> bool:
    """Exactly what Food's _verify_billing_webhook_signature does."""
    normalized = json.dumps(json.loads(raw_body), sort_keys=True, separators=(",", ":"))
    expected = base64.b64encode(
        hmac.new(WEBHOOK_SECRET.encode(), normalized.encode(), hashlib.sha256).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)


class FakeSubRepo:
    def __init__(self, sub): self.sub, self.updates = sub, []
    async def get_by_billing_sub_id(self, bid):
        return self.sub if self.sub and self.sub.billing_core_sub_id == bid else None
    async def get_by_store_id(self, sid):
        return self.sub if self.sub and self.sub.store_id == sid else None
    async def update(self, sub): self.updates.append(sub); return sub
    async def save(self, sub): self.sub = sub; return sub


class FakeStoreRepo:
    def __init__(self): self.plan_updates = []
    async def get_by_id(self, sid): return type("S", (), {"id": sid, "plan": SubscriptionPlan.starter})()
    async def update_plan(self, sid, plan): self.plan_updates.append((sid, plan))


# ═══════════════════════════════════════════════════════════════════════════════
# S1 — Assinatura paga: assinatura HMAC sobrevive ao round-trip
# ═══════════════════════════════════════════════════════════════════════════════

def test_S1_signature_roundtrip_holds():
    payload = SendInternalWebhookSubscription(
        event=InternalEventType.PAYMENT_RECEIVED,
        subscription_id=uuid4(),
        system_sub_id=f"{uuid4()}:pro:abc123def456",
        subscription_expires_at=date(2027, 8, 29),
        payment_date=date(2026, 7, 29),
    ).model_dump(mode="json")

    raw, sig = billing_sign_and_serialize(payload)
    assert food_verify(raw, sig), "Food rejeitaria a assinatura do Billing Core"


def test_S1b_signature_roundtrip_with_non_ascii():
    """Billing assina com ensure_ascii=True; httpx transmite UTF-8 cru."""
    payload = {"event": "PAYMENT_RECEIVED", "descricao": "Plano Pró — cobrança"}
    raw, sig = billing_sign_and_serialize(payload)
    assert food_verify(raw, sig), "Payload não-ASCII quebra a verificação de assinatura"


# ═══════════════════════════════════════════════════════════════════════════════
# S2 — Payload do Billing Core parseia no DTO do Food e ativa a loja
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_S2_payment_received_activates_store():
    store_id, billing_sub_id = uuid4(), str(uuid4())
    sub = StoreSubscription(
        store_id=store_id, plan=SubscriptionPlan.pro,
        status=SubscriptionStatus.pending, billing_core_sub_id=billing_sub_id,
        expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    repo, store_repo = FakeSubRepo(sub), FakeStoreRepo()

    wire = SendInternalWebhookSubscription(
        event=InternalEventType.PAYMENT_RECEIVED,
        subscription_id=UUID(billing_sub_id),
        system_sub_id=f"{store_id}:pro:abc123",
        subscription_expires_at=date(2026, 8, 29),
        payment_date=date(2026, 7, 29),
    ).model_dump(mode="json")

    parsed = BillingWebhookPayload.model_validate_json(json.dumps(wire))
    await HandleBillingWebhookUseCase(repo, store_repo).execute(parsed)

    assert sub.status == SubscriptionStatus.active
    assert store_repo.plan_updates == [(store_id, SubscriptionPlan.pro)]


# ═══════════════════════════════════════════════════════════════════════════════
# S3 — FORÇAR: webhook chega antes de o Food gravar billing_core_sub_id
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_S3_unknown_subscription_id_is_silently_dropped():
    """Se billing_core_sub_id ainda é NULL no Food, o fallback por system_sub_id
    precisa resolver a loja. system_sub_id do Food é composto -> UUID() falha."""
    store_id = uuid4()
    sub = StoreSubscription(
        store_id=store_id, plan=SubscriptionPlan.pro,
        status=SubscriptionStatus.pending,
        billing_core_sub_id=None,           # job ainda não tinha respondido
        expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    repo, store_repo = FakeSubRepo(sub), FakeStoreRepo()

    wire = SendInternalWebhookSubscription(
        event=InternalEventType.PAYMENT_RECEIVED,
        subscription_id=uuid4(),
        system_sub_id=f"{store_id}:pro:abc123",   # formato real emitido pelo Food
        subscription_expires_at=date(2026, 8, 29),
        payment_date=date(2026, 7, 29),
    ).model_dump(mode="json")

    parsed = BillingWebhookPayload.model_validate_json(json.dumps(wire))
    await HandleBillingWebhookUseCase(repo, store_repo).execute(parsed)

    assert sub.status == SubscriptionStatus.pending, "esperado: evento perdido"
    assert store_repo.plan_updates == [], "loja pagou e não foi liberada"


@pytest.mark.asyncio
async def test_S3b_fallback_works_only_if_system_sub_id_is_bare_uuid():
    """Prova que o fallback existe e funciona — só não com o formato do Food."""
    store_id = uuid4()
    sub = StoreSubscription(
        store_id=store_id, plan=SubscriptionPlan.pro,
        status=SubscriptionStatus.pending, billing_core_sub_id=None,
        expires_at=datetime.now(UTC) + timedelta(days=365),
    )
    repo, store_repo = FakeSubRepo(sub), FakeStoreRepo()
    parsed = BillingWebhookPayload.model_validate({
        "event": "PAYMENT_RECEIVED",
        "subscription_id": str(uuid4()),
        "system_sub_id": str(store_id),        # UUID puro
        "subscription_expires_at": "2026-08-29",
        "payment_date": "2026-07-29",
    })
    await HandleBillingWebhookUseCase(repo, store_repo).execute(parsed)
    assert sub.status == SubscriptionStatus.active


# ═══════════════════════════════════════════════════════════════════════════════
# S4 — FORÇAR: inadimplência (PAYMENT_OVERDUE) e estorno vindos do Asaas
# ═══════════════════════════════════════════════════════════════════════════════

def _billing_sub():
    return BillingSubscription(
        initial_date=datetime.now(timezone.utc), description="Plano Pro",
        system_subscription_id=f"{uuid4()}:pro:abc", gateway_subscription_id="gw-sub-1",
        gateway_provider=GatewayProvider.ASAAS, status=BillingSubStatus.ACTIVE,
        last_paid_date=None, from_system=System.NEECTIFY_FOOD,
        subscription_type=SubscriptionType.MONTHLY,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        id=uuid4(), value=Decimal("99.90"),
    )


def _billing_payment(paid=False):
    from app.domain.entities.payment import Payment
    from app.domain.enums.payment_type import PaymentType

    payment = Payment.create_subscription_payment(
        description="Pagamento relacionado a assinatura: Plano Pro",
        gateway=GatewayProvider.ASAAS, system_payment_id="sub-1:pay-1",
        provider_payment_id="pay-1", value=Decimal("99.90"),
        from_system=System.NEECTIFY_FOOD, subscription_id=uuid4(),
        payment_type=PaymentType.CREDIT_CARD,
    )
    payment.id = uuid4()
    if paid:
        payment.mark_as_paid(datetime.now(timezone.utc))
    return payment


class _PayRepo:
    def __init__(self, existing=None): self.existing = existing
    async def get_by_provider_id(self, _): return self.existing
    async def get_by_external_reference(self, _): return None
    async def save(self, p): return p


class _SubRepo:
    def __init__(self, s): self.subscription, self.saved = s, 0
    async def get_by_provider_id(self, _): return self.subscription
    async def get_by_provider_id_for_update(self, _): return self.subscription
    async def save(self, s): self.saved += 1; return s


class _EvtRepo:
    def __init__(self): self.saved = []
    async def get_by_event_id(self, _): return None
    async def save(self, e): self.saved.append(e); return e


class _Uow:
    async def commit(self): pass
    async def rollback(self): pass


@pytest.mark.asyncio
@pytest.mark.parametrize("event,already_paid,internal", [
    (EventType.PAYMENT_OVERDUE, False, "PAYMENT_OVERDUE"),
    (EventType.PAYMENT_REFUNDED, True, "PAYMENT_REFUNDED"),
    (EventType.PAYMENT_CHARGEBACK_REQUESTED, True, "PAYMENT_CHARGEBACK_REQUESTED"),
])
async def test_S4_lifecycle_events_are_mirrored_to_the_consumer(event, already_paid, internal):
    """Inadimplência, estorno e chargeback precisam virar evento interno."""
    sub = _billing_sub()
    svc = ProcessWebhookService(
        payment_repo=_PayRepo(_billing_payment(paid=already_paid)), sub_repo=_SubRepo(sub),
        uow=_Uow(), webhook_event_repo=_EvtRepo(),
    )
    payload = WebhookPayload(
        event=event, source_event_id="evt_1",
        details=Details(id="pay-1", subscription="gw-sub-1",
                        status=event.value.removeprefix("PAYMENT_"), value=Decimal("99.90")),
    )
    result = await svc.execute(GatewayProvider.ASAAS, payload)
    assert result is not None, f"{event.value} nao gerou notificacao"
    assert result.event.value == internal
    assert result.subscription_id == sub.id


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    "PAYMENT_OVERDUE", "PAYMENT_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED",
])
async def test_S4b_food_reacts_to_each_lifecycle_event(event):
    """Contrato fechado: todo evento que o Billing Core emite tem tratamento."""
    store_id = uuid4(); bid = str(uuid4())
    sub = StoreSubscription(store_id=store_id, plan=SubscriptionPlan.pro,
                            status=SubscriptionStatus.active, billing_core_sub_id=bid)
    repo = FakeSubRepo(sub)
    await HandleBillingWebhookUseCase(repo, FakeStoreRepo()).execute(
        BillingWebhookPayload(event=event, subscription_id=bid)
    )
    assert sub.status == SubscriptionStatus.overdue


def test_S4c_every_internal_event_has_a_consumer_branch():
    """Nenhum InternalEventType pode virar no-op silencioso no Food."""
    import inspect
    handled = inspect.getsource(HandleBillingWebhookUseCase.execute)
    for member in InternalEventType:
        if member is InternalEventType.PAYMENT_STATUS_UPDATED:
            continue  # generico: resolvido pelos campos de status, nao pelo nome
        assert member.value in handled, f"{member.value} sem tratamento no Food"


# ═══════════════════════════════════════════════════════════════════════════════
# S5 — FORÇAR: cancelamento. O Food espera SUBSCRIPTION_INACTIVATED.
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_S5_inactivated_event_downgrades_store_when_it_arrives():
    store_id = uuid4(); bid = str(uuid4())
    sub = StoreSubscription(store_id=store_id, plan=SubscriptionPlan.pro,
                            status=SubscriptionStatus.active, billing_core_sub_id=bid)
    repo, store_repo = FakeSubRepo(sub), FakeStoreRepo()
    wire = SendInternalWebhookSubscription(
        event=InternalEventType.SUBSCRIPTION_INACTIVATED,
        subscription_id=UUID(bid), system_sub_id=f"{store_id}:pro:abc",
        subscription_expires_at=date(2026, 8, 29), payment_date=None,
    ).model_dump(mode="json")
    await HandleBillingWebhookUseCase(repo, store_repo).execute(
        BillingWebhookPayload.model_validate_json(json.dumps(wire))
    )
    assert sub.status == SubscriptionStatus.cancelled
    assert store_repo.plan_updates == [(store_id, SubscriptionPlan.starter)]


def test_S5b_cancel_paths_emit_the_inactivation_event():
    """O cancelamento não pode depender do round-trip pelo Asaas: os dois
    caminhos que confirmam cancelamento precisam emitir a entrega interna."""
    import inspect
    from app.workers import tasks

    cancel_src = inspect.getsource(tasks.cancel_subscription_worker)
    assert "SUBSCRIPTION_INACTIVATED" in cancel_src
    assert "send_internal_webhook" in cancel_src

    recon_src = inspect.getsource(tasks.reconcile_gateway_operations_worker)
    cancel_branch = recon_src.split('elif op.operation_name == "cancel_subscription"')[1]
    cancel_branch = cancel_branch.split('elif op.operation_name ==')[0]
    assert "SUBSCRIPTION_INACTIVATED" in cancel_branch


# ═══════════════════════════════════════════════════════════════════════════════
# S6 — FORÇAR: job do Billing Core demora mais que o polling do Food
# ═══════════════════════════════════════════════════════════════════════════════

class SlowBillingClient:
    """Billing Core saudável, apenas mais lento que os 3,5s de polling do Food."""
    def __init__(self):
        self.created, self.cancelled, self.polled = [], [], []
        self.job_result = None                                    # ainda "processing"
    async def create_customer(self, **kw): return "cus_123"
    async def create_subscription(self, **kw):
        self.created.append(kw); return {"job_id": f"job-{len(self.created)}"}
    async def cancel_subscription(self, **kw): self.cancelled.append(kw); return {"job_id": "c1"}
    async def wait_for_job_result(self, job_id):
        self.polled.append(job_id); return self.job_result


class _FoodStoreRepo:
    async def get_by_id(self, sid): return type("S", (), {"id": sid})()
    async def update_plan(self, *a): pass


class _FoodUserRepo:
    def __init__(self, sid):
        self.owner = type("U", (), {
            "id": uuid4(), "name": "Dono", "email": "d@x.com",
            "document": "12345678901", "billing_provider_customer_id": None,
            "store_id": sid,
        })()
    async def get_owner_by_store_id(self, sid): return self.owner
    async def save(self, u): self.owner = u; return u


@pytest.mark.asyncio
async def test_S6_slow_job_does_not_create_a_second_asaas_subscription():
    """Job lento não pode virar cobrança dobrada: a retentativa reconsulta o job."""
    store_id = uuid4()
    repo = FakeSubRepo(None)
    billing = SlowBillingClient()
    uc = CreateSubscriptionUseCase(repo, _FoodStoreRepo(), _FoodUserRepo(store_id),
                                   billing, "https://api.neectify.com/api/v1/billing/webhook")

    first = await uc.execute(store_id, CreateSubscriptionInput(plan=SubscriptionPlan.pro))
    assert first.checkout_url is None, "lojista fica sem link nessa primeira volta"
    assert first.billing_job_id == "job-1"

    # Estado realmente persistido pela primeira tentativa.
    repo.sub = StoreSubscription(
        store_id=store_id, plan=SubscriptionPlan.pro, status=SubscriptionStatus.pending,
        billing_job_id=first.billing_job_id, billing_core_sub_id=None, checkout_url=None,
    )

    # O job termina; o lojista clica "assinar" de novo.
    billing.polled.clear()
    billing.job_result = {"subscription_id": "sub-1", "checkout_url": "https://asaas.com/i/p1"}
    second = await uc.execute(store_id, CreateSubscriptionInput(plan=SubscriptionPlan.pro))

    assert len(billing.created) == 1, "nenhuma segunda assinatura pode ser criada"
    assert billing.polled == ["job-1"], "a retentativa precisa reconsultar o job"
    assert second.checkout_url == "https://asaas.com/i/p1", "link recuperado"
    assert second.billing_core_sub_id == "sub-1"


# ═══════════════════════════════════════════════════════════════════════════════
# S7 — FORÇAR: vencimento sem webhook
# ═══════════════════════════════════════════════════════════════════════════════

def test_S7_expired_paid_subscription_is_blocked():
    sub = StoreSubscription(
        store_id=uuid4(), plan=SubscriptionPlan.pro,
        status=SubscriptionStatus.active,
        expires_at=datetime.now(UTC) - timedelta(days=400),   # venceu há muito
    )
    assert sub.is_active is False
    assert sub.is_blocked is True


def test_S7b_renewal_lag_does_not_block_a_paying_store():
    """A confirmação do cartão chega depois do vencimento — não bloquear ainda."""
    sub = StoreSubscription(
        store_id=uuid4(), plan=SubscriptionPlan.pro,
        status=SubscriptionStatus.active,
        expires_at=datetime.now(UTC) - timedelta(hours=6),
    )
    assert sub.is_active is True
    assert sub.is_blocked is False


# ═══════════════════════════════════════════════════════════════════════════════
# S9 — FORÇAR: renovação mensal colide na idempotência do próprio Food
# ═══════════════════════════════════════════════════════════════════════════════

def food_event_key(payload: BillingWebhookPayload, webhook_id=None, x_request_id=None) -> str:
    """Derivação real do Food — importada, nunca copiada."""
    return build_billing_event_key(payload, webhook_id=webhook_id, request_id=x_request_id)


def _renewal_wire(sub_uuid, delivery_id, expires, paid):
    """Entrega real do Billing Core + o header que ele de fato envia."""
    body = SendInternalWebhookSubscription(
        event=InternalEventType.PAYMENT_RECEIVED, subscription_id=sub_uuid,
        system_sub_id=f"{uuid4()}:pro:abc", subscription_expires_at=expires,
        payment_date=paid,
    ).model_dump(mode="json")
    headers = {"X-Webhook-Id": str(delivery_id), "X-Webhook-Event": "PAYMENT_RECEIVED"}
    return body, headers


def test_S9_the_header_billing_core_sends_is_the_one_food_reads():
    sub_uuid = uuid4()
    m1, h1 = _renewal_wire(sub_uuid, uuid4(), date(2026, 8, 29), date(2026, 7, 29))
    m2, h2 = _renewal_wire(sub_uuid, uuid4(), date(2026, 9, 29), date(2026, 8, 29))

    assert h1["X-Webhook-Id"] != h2["X-Webhook-Id"]

    k1 = food_event_key(BillingWebhookPayload.model_validate(m1), webhook_id=h1["X-Webhook-Id"])
    k2 = food_event_key(BillingWebhookPayload.model_validate(m2), webhook_id=h2["X-Webhook-Id"])
    assert k1 != k2, "cada entrega precisa de chave própria"


def test_S9b_renewals_survive_reserve():
    """Simula SQLAlchemyIdempotencyRepository.reserve() em dois ciclos."""
    from src.infrastructure.repositories.idempotency_repository import (
        IdempotencyConflictError, canonical_request_hash,
    )
    sub_uuid = uuid4()
    m1, h1 = _renewal_wire(sub_uuid, uuid4(), date(2026, 8, 29), date(2026, 7, 29))
    m2, h2 = _renewal_wire(sub_uuid, uuid4(), date(2026, 9, 29), date(2026, 8, 29))

    store: dict = {}

    def reserve(payload, webhook_id):
        key = (str(payload.subscription_id), "billing.webhook",
               food_event_key(payload, webhook_id=webhook_id))
        h = canonical_request_hash(payload.model_dump(mode="json"))
        if key in store:
            if store[key]["hash"] != h:
                raise IdempotencyConflictError()
            return False
        store[key] = {"hash": h, "status": "succeeded"}
        return True

    p1 = BillingWebhookPayload.model_validate(m1)
    p2 = BillingWebhookPayload.model_validate(m2)
    assert reserve(p1, h1["X-Webhook-Id"]) is True      # 1º mês processa
    assert reserve(p2, h2["X-Webhook-Id"]) is True      # 2º mês também
    assert reserve(p2, h2["X-Webhook-Id"]) is False     # reentrega deduplica


def test_S9c_renewals_survive_even_without_the_header():
    """Defesa em profundidade: o fallback também precisa distinguir ciclos."""
    sub_uuid = uuid4()
    m1, _ = _renewal_wire(sub_uuid, uuid4(), date(2026, 8, 29), date(2026, 7, 29))
    m2, _ = _renewal_wire(sub_uuid, uuid4(), date(2026, 9, 29), date(2026, 8, 29))
    k1 = food_event_key(BillingWebhookPayload.model_validate(m1))
    k2 = food_event_key(BillingWebhookPayload.model_validate(m2))
    assert k1 != k2


# ═══════════════════════════════════════════════════════════════════════════════
# S8 — FORÇAR: contrato de entrada (webhook_link) do Food no schema do Billing
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("backend_url,ok", [
    ("https://api.neectify.com", True),
    ("http://api.neectify.com", False),     # BACKEND_URL sem TLS
    ("http://localhost:8000", False),       # default de src/settings.py
])
def test_S8_webhook_link_scheme_and_host(monkeypatch, backend_url, ok):
    from app.infra import config as billing_config
    monkeypatch.setattr(billing_config.settings, "ALLOWED_INTERNAL_WEBHOOK_HOSTS",
                        ["neectify.com"], raising=False)
    body = {
        "customer_provider_id": "cus_123", "value": "99.90",
        "subscription_type": "MONTHLY", "next_due_date": None,
        "description": "Neectify Food — Plano Pro", "system": "neectify_food",
        "system_sub_id": f"{uuid4()}:pro:abc123",
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
        "webhook_link": f"{backend_url}/api/v1/billing/webhook",
    }
    if ok:
        assert CreateSubscriptionRequest.model_validate(body).system is System.NEECTIFY_FOOD
    else:
        with pytest.raises(Exception):
            CreateSubscriptionRequest.model_validate(body)
