# Payments Asaas Checkout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `POST /v1/payments` create a detached Asaas Checkout, remove payment links and direct standalone charges, and update local payments from Checkout webhooks.

**Architecture:** A checkout-specific use case calls `POST /v3/checkouts` and persists the returned checkout ID/link in the existing `Payment` aggregate. `POST /v1/payments` only enqueues that use case; `CHECKOUT_*` webhooks are the financial source of truth. Marketfy adopts the same request contract in the coordinated release.

**Tech Stack:** Python, FastAPI, Pydantic v2, ARQ, Redis, SQLAlchemy async, httpx, pytest, Asaas API v3.

## Global Constraints

- Keep `POST /v1/payments` as the only public endpoint for checkout avulso; do not create `/v1/checkouts`.
- Send exactly `billingTypes: ["PIX", "CREDIT_CARD"]` and `chargeTypes: ["DETACHED"]` in v1.
- Require at least one item, a total equal to `sum(quantity * value)`, 10–1440 minutes of expiry, and HTTPS callbacks permitted by `ALLOWED_CHECKOUT_REDIRECT_HOSTS`.
- Use `checkout:{system}:{system_payment_id}` as `externalReference`; reject values over 200 characters.
- Persist `checkout_id` as `Payment.provider_payment_id`, returned `link` as `Payment.checkout_link`, and set `pending`/`UNDEFINED` initially.
- Never release a purchase from a callback or job result. Only idempotent `CHECKOUT_*` webhooks can change state and create the signed internal webhook.
- Map `CHECKOUT_PAID` → `paid`, `CHECKOUT_CANCELED` → `canceled`, `CHECKOUT_EXPIRED` → `expired`.
- Do not enqueue `reconcile_pending_payment_worker`: it accepts a charge ID, not a checkout ID.
- Remove all active payment-link and direct standalone-payment creation code. Keep subscriptions and `GET /v1/payments/{payment_id}` intact.
- Drain legacy ARQ jobs before removing their handlers; deploy API, worker and Marketfy together.

---

## File Structure

| Path | Responsibility |
|---|---|
| `app/application/dtos/request/checkout.py` | Checkout and line-item DTOs. |
| `app/application/dtos/response/checkout.py` | Job result DTO. |
| `app/application/use_cases/create_checkout.py` | Gateway operation, idempotency and persistence. |
| `app/web/schemas/payment.py` | New public payments body and validation. |
| `app/web/routes/payments.py` | Enqueue checkout creation; retain GET status. |
| `app/application/interfaces/gateway_provider.py` | Typed checkout gateway interface. |
| `app/infra/interfaces/asaas_provider.py` | `/checkouts` translation and Checkout event normalization. |
| `app/workers/tasks.py`, `app/workers/worker.py` | Checkout job and legacy worker removal. |
| `app/application/dtos/request/webhook.py`, `app/application/use_cases/process_webhook.py` | Checkout event transitions. |
| `app/domain/entities/payment.py` | Explicit `expired` transition. |
| `../marketfy/backend/app/infra/clients/billing_core_client.py` | Consumer request contract. |
| `../marketfy/backend/app/application/services/fiscal/fiscal_credits_service.py` | Credit item and callback construction. |

---

### Task 1: Define and validate the checkout contract

**Files:**
- Create: `app/application/dtos/request/checkout.py`
- Create: `app/application/dtos/response/checkout.py`
- Modify: `app/web/schemas/payment.py`
- Modify: `app/infra/config.py`, `.env.example`
- Test: `tests/test_api_contracts.py`

**Interfaces:**
- Produces `CheckoutItemDTO(external_reference: str, name: str, description: str, quantity: int, value: Decimal)`.
- Produces `CreateCheckoutDTO(system, system_payment_id, description, value, minutes_to_expire, items, success_url, cancel_url, expired_url, webhook_link)`.
- Produces `CreateCheckoutResponse(payment_id: UUID, checkout_url: str, payment_status: PaymentStatus)`.

- [ ] **Step 1: Add failing request tests**

```python
def checkout_payload(**overrides):
    payload = {
        "system": "neectify_shop", "system_payment_id": "order-123",
        "description": "Pacote de créditos", "value": "72.00",
        "minutes_to_expire": 30,
        "items": [{"external_reference": "pack-100", "name": "100 créditos", "description": "Créditos fiscais", "quantity": 1, "value": "72.00"}],
        "success_url": "https://app.neectify.local/billing/success",
        "cancel_url": "https://app.neectify.local/billing/cancel",
        "expired_url": "https://app.neectify.local/billing/expired",
        "webhook_link": "https://hooks.neectify.local/billing/payment",
    }
    payload.update(overrides)
    return payload


def test_payment_checkout_rejects_mismatched_total():
    with pytest.raises(ValidationError, match="value deve ser igual"):
        CreatePaymentRequest.model_validate(checkout_payload(value="71.99"))


@pytest.mark.parametrize("minutes", [9, 1441])
def test_payment_checkout_rejects_invalid_expiration(minutes):
    with pytest.raises(ValidationError):
        CreatePaymentRequest.model_validate(checkout_payload(minutes_to_expire=minutes))
```

Add direct schema/route assertions for no items, zero quantity/value, HTTP or unallowed callback hosts, `system` different from `X-System`, and legacy `customer_provider_id`/`due_date` rejected with 422.

- [ ] **Step 2: Verify the new tests fail**

Run: `python -m pytest tests/test_api_contracts.py -k "checkout or payment" -q`

Expected: FAIL because the legacy schema requires a customer and due date.

- [ ] **Step 3: Implement DTOs, config and schema**

Create the DTOs:

```python
class CheckoutItemDTO(BaseModel):
    external_reference: str
    name: str
    description: str = ""
    quantity: int
    value: Decimal


class CreateCheckoutDTO(BaseModel):
    model_config = ConfigDict(use_enum_values=False)
    system: System
    system_payment_id: str
    description: str
    value: Decimal
    minutes_to_expire: int
    items: list[CheckoutItemDTO]
    success_url: str
    cancel_url: str
    expired_url: str
    webhook_link: str
```

Replace `CreatePaymentRequest` legacy fields with these fields and set `model_config = ConfigDict(use_enum_values=False, extra="forbid")`, so legacy `customer_provider_id` and `due_date` are rejected rather than silently ignored. Strip item text, require positive quantity/value and a non-empty list, compare Decimal totals exactly, and reject a `system_payment_id` that would make `checkout:{system}:{system_payment_id}` exceed 200 characters. Reuse the existing `urlparse` HTTPS/host rule for every callback. Add `ALLOWED_CHECKOUT_REDIRECT_HOSTS: list[str] = Field(default_factory=list)` to `Settings`, reject an empty list in the request validation, require the setting in production in `validate_runtime()`, and add an example to `.env.example`. `to_worker_payload()` must be `model_dump(mode="json")`.

- [ ] **Step 4: Run the focused contract test**

Run: `python -m pytest tests/test_api_contracts.py -k "checkout or payment" -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/dtos/request/checkout.py app/application/dtos/response/checkout.py app/web/schemas/payment.py app/infra/config.py .env.example tests/test_api_contracts.py
git commit -m "feat: define payments checkout contract"
```

### Task 2: Implement the Asaas Checkout gateway

**Files:**
- Modify: `app/application/interfaces/gateway_provider.py`
- Modify: `app/infra/interfaces/asaas_provider.py`
- Test: `tests/test_asaas_provider.py`

**Interfaces:**
- Produces `CreateCheckoutGatewayResponse(checkout_id: str, checkout_url: str, status: str, external_reference: str | None)`.
- Produces `InterfaceGateway.create_checkout(*, billing_types, charge_types, minutes_to_expire, external_reference, callback, items)`.

- [ ] **Step 1: Add the failing provider test**

```python
@pytest.mark.asyncio
async def test_asaas_provider_creates_detached_checkout_payload():
    provider = AsaasProvider()
    fake_api = FakeAsaasAPI({
        "id": "checkout_123", "link": "https://sandbox.asaas.com/checkoutSession/show/checkout_123",
        "status": "ACTIVE", "externalReference": "checkout:marketfy:order-123",
    })
    provider.asaas = fake_api

    response = await provider.create_checkout(
        billing_types=["PIX", "CREDIT_CARD"], charge_types=["DETACHED"], minutes_to_expire=30,
        external_reference="checkout:marketfy:order-123",
        callback={"successUrl": "https://app.test/s", "cancelUrl": "https://app.test/c", "expiredUrl": "https://app.test/e"},
        items=[{"externalReference": "pack-100", "name": "100 créditos", "description": "", "quantity": 1, "value": 72.0}],
    )

    assert fake_api.endpoint == "/checkouts"
    assert fake_api.payload["billingTypes"] == ["PIX", "CREDIT_CARD"]
    assert fake_api.payload["chargeTypes"] == ["DETACHED"]
    assert response.checkout_id == "checkout_123"
```

Also test missing `id`, `link`, `status`, or a mismatched `externalReference` raises `DomainError`.

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_asaas_provider.py -k checkout -q`

Expected: FAIL because `create_checkout` does not exist.

- [ ] **Step 3: Add the interface and provider method**

```python
@dataclass
class CreateCheckoutGatewayResponse:
    checkout_id: str
    checkout_url: str
    status: str
    external_reference: str | None


payload = {
    "billingTypes": billing_types, "chargeTypes": charge_types,
    "minutesToExpire": minutes_to_expire, "externalReference": external_reference,
    "callback": callback, "items": items,
}
response = await self.asaas.post("/checkouts", payload)
```

Require `id`, `link`, `status`, and the matching external reference before constructing the response. Delete `CreatePaymentLinkGatewayResponse` and `create_payment_link` from both the interface and `AsaasProvider`; do not alter subscription gateway methods.

- [ ] **Step 4: Run provider regression tests**

Run: `python -m pytest tests/test_asaas_provider.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/interfaces/gateway_provider.py app/infra/interfaces/asaas_provider.py tests/test_asaas_provider.py
git commit -m "feat: create detached checkouts through Asaas"
```

### Task 3: Persist an idempotent checkout

**Files:**
- Create: `app/application/use_cases/create_checkout.py`
- Create: `tests/test_create_checkout_use_case.py`
- Delete: `app/application/use_cases/create_payment.py`, `tests/test_create_payment_use_case.py`

**Interfaces:**
- Produces `CreateCheckout.execute(request: CreateCheckoutDTO, gateway_provider: GatewayProvider) -> CreateCheckoutResponse`.
- Uses `create_checkout:{system}:{system_payment_id}` as the `GatewayOperation` dedupe key.

- [ ] **Step 1: Add failing use-case tests**

```python
@pytest.mark.asyncio
async def test_create_checkout_persists_gateway_checkout_and_returns_link():
    response = await service.execute(make_request(), GatewayProvider.ASAAS)
    assert gateway.create_checkout_called == 1
    assert gateway.last_kwargs["billing_types"] == ["PIX", "CREDIT_CARD"]
    assert gateway.last_kwargs["charge_types"] == ["DETACHED"]
    assert payment_repo.saved[0].provider_payment_id == "checkout_123"
    assert payment_repo.saved[0].external_reference == "checkout:marketfy:order-123"
    assert response.payment_status == PaymentStatus.PENDING
```

Cover existing local payment reuse, failed operation retry without a gateway reference, completed operation without a local payment (raises `DomainError`), and local-save failure after remote create (`REQUIRES_RECONCILIATION`).

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_create_checkout_use_case.py -q`

Expected: FAIL because `CreateCheckout` is missing.

- [ ] **Step 3: Implement `CreateCheckout`**

```python
external_reference = f"checkout:{request.system.value}:{request.system_payment_id}"
dedupe_key = f"create_checkout:{request.system.value}:{request.system_payment_id}"
existing_payment = await self.payment_repo.get_by_system_ref(request.system_payment_id, request.system)
if existing_payment:
    return CreateCheckoutResponse(payment_id=existing_payment.id, checkout_url=existing_payment.checkout_link, payment_status=existing_payment.payment_status)
```

Persist/commit `GatewayOperation(operation_name="create_checkout", dedupe_key=dedupe_key, ...)` before the remote call. Call `create_checkout` with fixed type lists and camelCase callback/item dictionaries. Create `Payment.create_standalone_payment` with checkout ID/link, no due date and the external reference; then set `payment_type = UNDEFINED`, `payment_status = PENDING`, save, mark the operation complete and commit. Preserve the current rollback plus `mark_requires_reconciliation` path if local persistence fails after remote success.

- [ ] **Step 4: Run the focused test**

Run: `python -m pytest tests/test_create_checkout_use_case.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/use_cases/create_checkout.py tests/test_create_checkout_use_case.py
git rm app/application/use_cases/create_payment.py tests/test_create_payment_use_case.py
git commit -m "feat: persist idempotent payment checkouts"
```

### Task 4: Switch the route and worker; remove payment links

**Files:**
- Modify: `app/web/routes/payments.py`, `app/workers/tasks.py`, `app/workers/worker.py`, `app/web/main.py`
- Modify: `tests/test_api_contracts.py`, `tests/test_payment_workers.py`
- Delete: `app/web/routes/payment_links.py`, `app/web/schemas/payment_link.py`, `app/application/dtos/request/payment.py`, `app/application/dtos/request/payment_link.py`, `app/application/dtos/response/payment_link.py`, `app/application/use_cases/create_payment_link.py`, `tests/test_create_payment_link_use_case.py`

**Interfaces:**
- Produces ARQ job `workers:tasks.create_checkout_worker(dto_dict)`.
- Uses `checkout_create` as Redis idempotency namespace and `create_checkout_worker` in job metadata.

- [ ] **Step 1: Add failing route and worker tests**

```python
assert redis.enqueued_jobs[0][0][0] == "workers:tasks.create_checkout_worker"
assert "customer_provider_id" not in redis.enqueued_jobs[0][0][1]
assert response.status_code == 202

response = await tasks.create_checkout_worker(ctx, checkout_payload)
assert response["status"] == "success"
assert response["result"]["checkout_url"].startswith("https://")
assert not [job for job in fake_redis.enqueued_jobs if job[0][0] == "workers:tasks.reconcile_pending_payment_worker"]
```

Also assert `POST /v1/payment-links` returns 404 after route removal.

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_api_contracts.py tests/test_payment_workers.py -k "checkout or payment_link" -q`

Expected: FAIL because the legacy workers and payment-links router remain.

- [ ] **Step 3: Implement the public/worker switch**

In `payments.py`, retain auth and GET status but set `namespace = "checkout_create"` and enqueue only:

```python
job = await redis.enqueue_job("workers:tasks.create_checkout_worker", payload.to_worker_payload())
```

Set `job_name="create_checkout_worker"`, preserve `resource_type="payment"`, and do not schedule reconciliation. Add `create_checkout_worker(ctx, dto_dict)` using the current payment-link worker’s terminal-4xx/transient-5xx behavior; construct `CreateCheckoutDTO`, `PaymentRepositoryINFRA`, `GatewayOperationRepositoryINFRA`, `UowProvider`, `GetGatewayInfra`, and `CreateCheckout`, then call `execute(dto, GatewayProvider.ASAAS)` and return `_dump_result(result)`.

Remove both legacy creation workers, their registrations, payment-link router import/tag/include, and all explicitly listed files.

- [ ] **Step 4: Run route and worker regression tests**

Run: `python -m pytest tests/test_api_contracts.py tests/test_payment_workers.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/routes/payments.py app/workers/tasks.py app/workers/worker.py app/web/main.py tests/test_api_contracts.py tests/test_payment_workers.py
git rm app/web/routes/payment_links.py app/web/schemas/payment_link.py app/application/dtos/request/payment.py app/application/dtos/request/payment_link.py app/application/dtos/response/payment_link.py app/application/use_cases/create_payment_link.py tests/test_create_payment_link_use_case.py
git commit -m "feat: create checkouts from payments route"
```

### Task 5: Normalize and process Checkout webhooks

**Files:**
- Modify: `app/application/dtos/request/webhook.py`, `app/infra/interfaces/asaas_provider.py`, `app/application/use_cases/process_webhook.py`, `app/domain/entities/payment.py`
- Test: `tests/test_asaas_webhook_normalization.py`, `tests/test_process_webhook_use_case.py`, `tests/test_domain_entities.py`

**Interfaces:**
- Produces four `EventType.CHECKOUT_*` values.
- Produces `Payment.mark_as_expired()`.
- Returns `PAYMENT_STATUS_UPDATED` only when a checkout state actually changes.

- [ ] **Step 1: Add failing normalization, domain and service tests**

```python
payload = {
    "id": "evt-checkout-1", "event": "CHECKOUT_PAID",
    "checkout": {"id": "checkout_123", "status": "PAID", "externalReference": "checkout:marketfy:order-123", "items": [{"quantity": 1, "value": 72}]},
}
normalized = provider.normalize_webhook(payload)
assert normalized.source_event_id == "evt-checkout-1"
assert normalized.details.id == "checkout_123"
assert normalized.details.external_reference == "checkout:marketfy:order-123"
assert normalized.details.value == Decimal("72")
```

Test each event: created produces no internal event; paid becomes paid; canceled becomes canceled; expired becomes expired; duplicate source event returns `None`; unknown checkout is marked processed and returns `None`; and an out-of-order terminal event (for example `CHECKOUT_CANCELED` after `CHECKOUT_EXPIRED`) is marked processed without a second internal delivery. Test `mark_as_expired` only accepts `PENDING`/`OVERDUE`.

- [ ] **Step 2: Verify failure**

Run: `python -m pytest tests/test_asaas_webhook_normalization.py tests/test_process_webhook_use_case.py tests/test_domain_entities.py -k "checkout or expired" -q`

Expected: FAIL because Checkout events and expiration transition are absent.

- [ ] **Step 3: Implement the webhook behavior**

Add `CHECKOUT_CREATED`, `CHECKOUT_PAID`, `CHECKOUT_CANCELED`, `CHECKOUT_EXPIRED` to `EventType`. In `normalize_webhook`, branch on `payload["checkout"]` before existing payment/subscription handling; map checkout ID/status/reference and calculate value from items when needed.

Add:

```python
def mark_as_expired(self):
    if self.payment_status not in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:
        raise DomainError("Nao e possivel expirar esse pagamento.")
    self.payment_status = PaymentStatus.EXPIRED
    self.updated_at = datetime.now(timezone.utc)
```

Handle `CHECKOUT_*` before generic standalone payment processing. Lookup first by checkout ID, then external reference; never overwrite the stored checkout ID with a charge ID. Mark `CHECKOUT_CREATED` processed without a notification. For the remaining events, invoke the matching aggregate transition only when the target state differs. If a terminal event conflicts with the current terminal state, log the conflict, mark the webhook processed and do not emit a second delivery. Save only when changed, persist the webhook event and commit; return `PAYMENT_STATUS_UPDATED` only on an actual transition.

- [ ] **Step 4: Run webhook regression tests**

Run: `python -m pytest tests/test_asaas_webhook_normalization.py tests/test_process_webhook_use_case.py tests/test_domain_entities.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/dtos/request/webhook.py app/infra/interfaces/asaas_provider.py app/application/use_cases/process_webhook.py app/domain/entities/payment.py tests/test_asaas_webhook_normalization.py tests/test_process_webhook_use_case.py tests/test_domain_entities.py
git commit -m "feat: synchronize Asaas checkout webhooks"
```

### Task 6: Migrate Marketfy and complete verification

**Files:**
- Modify: `../marketfy/backend/app/infra/clients/billing_core_client.py`
- Modify: `../marketfy/backend/app/application/services/fiscal/fiscal_credits_service.py`
- Modify: `../marketfy/backend/app/infra/config/settings.py`
- Modify: `../marketfy/backend/tests/unit/test_billing_core_client.py`, `../marketfy/backend/tests/unit/test_fiscal_credits_service.py`, `../marketfy/backend/tests/unit/test_fiscal_credits_checkout.py`, `../marketfy/backend/tests/unit/test_fiscal_credits_pr7_pr8.py`
- Modify: `docs/API.md`, `docs/INTEGRATION.md`, `docs/Onboarding_SaaS.md`, `docs/Fluxos.md`, `docs/Ambiente.md`, `README.md`

**Interfaces:**
- Produces `BillingCoreClient.create_payment(*, value, description, system, system_payment_id, webhook_link, idempotency_key, items, success_url, cancel_url, expired_url, minutes_to_expire)`.

- [ ] **Step 1: Add failing Marketfy tests**

Assert the client sends `/v1/payments` with no customer, billing type, due date or payment-link due limit:

```python
assert request.url.path == "/v1/payments"
assert request.json()["items"] == [{
    "external_reference": "pack-100", "name": "100 créditos NF-e",
    "description": "Créditos para emissão fiscal", "quantity": 1, "value": "72.00",
}]
assert request.json()["value"] == "72.00"
```

For fixed and custom packages, assert `FiscalCreditsService` calls `create_payment`, uses a one-item total equal to package price, derives `/billing/success`, `/billing/cancel`, `/billing/expired` from `PUBLIC_FRONTEND_URL`, and saves the returned job ID.

- [ ] **Step 2: Verify Marketfy tests fail**

Run from `../marketfy/backend`: `python -m pytest tests/unit/test_billing_core_client.py tests/unit/test_fiscal_credits_service.py tests/unit/test_fiscal_credits_checkout.py tests/unit/test_fiscal_credits_pr7_pr8.py -q`

Expected: FAIL because the service still calls `create_payment_link`.

- [ ] **Step 3: Implement the consumer change**

Replace the `BillingCoreClient.create_payment` signature/payload with:

```python
payload = {
    "value": value, "description": description, "system": system,
    "system_payment_id": system_payment_id, "webhook_link": webhook_link,
    "minutes_to_expire": minutes_to_expire, "items": items,
    "success_url": success_url, "cancel_url": cancel_url, "expired_url": expired_url,
}
```

Delete `create_payment_link`. Replace `BILLING_CORE_PAYMENT_DUE_DAYS` with `BILLING_CORE_CHECKOUT_EXPIRATION_MINUTES: int = 30`. In both fiscal-credit purchase methods build the one package item, pass callbacks and the new setting, while retaining the package UUID as payment ID and idempotency key.

- [ ] **Step 4: Run Marketfy regression tests**

Run from `../marketfy/backend`: `python -m pytest tests/unit/test_billing_core_client.py tests/unit/test_fiscal_credits_service.py tests/unit/test_fiscal_credits_checkout.py tests/unit/test_fiscal_credits_pr7_pr8.py -q`

Expected: PASS.

- [ ] **Step 5: Update docs and perform the final verification**

Document the Task 1 body and async job result under `/v1/payments`; state that callbacks never grant access and `CHECKOUT_PAID` is required. Document `ALLOWED_CHECKOUT_REDIRECT_HOSTS` and all four Asaas events. Remove active payment-link/direct-charge documentation.

Run: `python -m pytest -q`

Expected: PASS.

Run: `rg -n -i "create_payment_link|/v1/payment-links|create_payment_worker|create_payment_link_worker" app tests`

Expected: no matches.

Run: `rg -n -i "POST /v1/payment-links|payment link|pagamento avulso.*customer" docs/API.md docs/INTEGRATION.md docs/Onboarding_SaaS.md docs/Fluxos.md docs/Ambiente.md README.md`

Expected: no matches; historical migration/spec records are deliberately excluded.

- [ ] **Step 6: Commit Billing Core and Marketfy changes separately**

```bash
# Billing Core repository
git add docs/API.md docs/INTEGRATION.md docs/Onboarding_SaaS.md docs/Fluxos.md docs/Ambiente.md README.md docs/superpowers/specs/2026-07-20-payments-asaas-checkout-design.md
git commit -m "docs: document payments checkout flow"

# Marketfy repository
git add app/infra/clients/billing_core_client.py app/application/services/fiscal/fiscal_credits_service.py app/infra/config/settings.py tests/unit/test_billing_core_client.py tests/unit/test_fiscal_credits_service.py tests/unit/test_fiscal_credits_checkout.py tests/unit/test_fiscal_credits_pr7_pr8.py
git commit -m "feat: create fiscal credit checkouts through payments"
```

- [ ] **Step 7: Execute rollout checks**

1. Drain queued `workers:tasks.create_payment_worker` and `workers:tasks.create_payment_link_worker` jobs before deploying removed handlers.
2. Configure `ALLOWED_CHECKOUT_REDIRECT_HOSTS` for the Marketfy frontend, and configure Asaas webhooks with `CHECKOUT_CREATED`, `CHECKOUT_CANCELED`, `CHECKOUT_EXPIRED`, `CHECKOUT_PAID`.
3. Deploy Billing Core API plus worker atomically, then Marketfy.
4. In Sandbox: create a PIX/card checkout, repeat the idempotency key, poll its job, open the Asaas link, validate callbacks do not activate credits, then validate each Checkout webhook produces the expected local and signed internal state.
5. Monitor dead-letter jobs, incomplete `GatewayOperation`s, `WebhookEvent` idempotency records and internal delivery failures during the release window.

## Plan Self-Review

- **Spec coverage:** Tasks 1–4 cover contract, gateway, persistence, idempotency, worker and payment-link removal. Task 5 covers every Checkout lifecycle event. Task 6 covers the identified consumer, docs, tests and rollout.
- **Placeholder scan:** Every task contains exact files, expected test outcomes, function names, payloads and commands.
- **Type consistency:** The schema creates `CreateCheckoutDTO`; `CreateCheckout.execute` returns `CreateCheckoutResponse`; the checkout worker serializes it; Marketfy sends the same payload; and Checkout webhooks update the stored `Payment` by checkout ID/reference.
