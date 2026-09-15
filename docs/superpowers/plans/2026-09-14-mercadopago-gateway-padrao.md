# Mercado Pago como gateway padrão — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar o Mercado Pago o gateway padrão do Billing Core para customers, checkouts avulsos e assinaturas novas, mantendo o adapter do Asaas funcionando para os registros que já estão nele.

**Architecture:** Um adapter novo, `MercadoPagoProvider`, implementa a `InterfaceGateway` existente e traduz os recursos do Mercado Pago para o vocabulário que os use cases já usam (`ACTIVE`, `RECEIVED`, `CHECKOUT_PAID`...). Por isso as rotas internas, os DTOs e quase todos os use cases ficam intactos. O gateway de cada registro continua gravado em `gateway_provider`/`gateway`. Só o que é **criado** passa a usar `settings.DEFAULT_GATEWAY_PROVIDER`. O Mercado Pago envia notificações só com o id do recurso, então elas entram por um endpoint técnico próprio e são resolvidas (buscadas na API) no worker antes de seguir o fluxo normal de `process_webhook`.

**Tech Stack:** Python 3.13, FastAPI 0.115, ARQ 0.27, SQLAlchemy 2 async, httpx 0.28, pydantic-settings, pytest (`asyncio_mode = auto`).

**Spec:** Este documento é a especificação. Ele parte da análise de viabilidade feita na sessão de 2026-09-14 e das três diretrizes do usuário:
1. Neectify Food ainda não tem clientes, então não é bloqueante.
2. As estruturas de rotas continuam funcionando como hoje; a mudança fica no tratamento dos adapters.
3. O comportamento do Mercado Pago segue a documentação atual, consultada via MCP oficial em 2026-09-14 (ver "Referências do Mercado Pago").

## Global Constraints

- Nenhuma rota interna existente muda de path, payload, header obrigatório ou formato de resposta: `POST /v1/customers`, `POST /v1/payments`, `GET /v1/payments/{id}`, `POST /v1/subscriptions`, `POST /v1/subscriptions/{id}/cancel`, `GET /v1/jobs/{id}`.
- `POST /v1/webhooks/asaas` continua exatamente como está.
- O adapter do Asaas continua registrado. Registros com `gateway_provider=asaas` continuam sendo operados pelo Asaas.
- Sem dependência nova: o cliente HTTP do Mercado Pago usa `httpx`, como o do Asaas. Nada de SDK `mercadopago`.
- Sem migration: `gateway_provider` e `gateway` são `String(50)` via `EnumValueType`.
- Mensagens de erro e logs seguem o padrão do repo: português sem acento (`"Checkout do Mercado Pago retornou id divergente."`).
- Toda chamada que cria recurso no Mercado Pago envia `X-Idempotency-Key`.
- Suíte completa verde ao fim de cada task: `python -m pytest -q`.
- Commits no padrão convencional (`feat:`, `fix:`, `refactor:`, `docs:`, `test:`), terminando com a linha `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## Decisões de desenho

| Decisão | Motivo |
|---|---|
| Traduzir o Mercado Pago para o vocabulário do Asaas **dentro do adapter** | Atende "mudar só o tratamento nos adapters". `process_webhook`, a reconciliação e o cancelamento comparam strings como `ACTIVE`/`RECEIVED`; o adapter entrega essas strings. |
| **Uma rota técnica nova:** `POST /v1/webhooks/mercadopago` | O Mercado Pago não consegue chamar `/v1/webhooks/asaas`: autentica com HMAC em `x-signature`, não com `asaas-access-token`, e o corpo só traz `data.id`. É uma rota de entrada do gateway, não um contrato consumido por Marketfy/Food. |
| `InterfaceGateway.resolve_webhook()` async com implementação padrão `return self.normalize_webhook(payload)` | O Asaas não muda. O Mercado Pago sobrescreve para buscar o recurso na API. |
| Assinatura no Mercado Pago = `preapproval` sem plano, com `status: "pending"` | Não temos `card_token_id` no fluxo atual. A doc diz que, sem meio de pagamento, a assinatura fica `pending` e o pagador conclui por link (`init_point`). O `init_point` vira o `checkout_url` devolvido hoje. |
| Pagamento local inicial da assinatura nasce com `provider_payment_id = <id do preapproval>` e é **revinculado** quando chega a primeira fatura | `CreateSubscription` exige um pagamento para responder. O Asaas cria a cobrança na hora; o Mercado Pago só depois da autorização. É a única mudança de use case ligada a assinatura (Task 10), e não afeta o Asaas porque um id de pagamento dele nunca é igual a um id de assinatura. |
| Checkout avulso = preferência do Checkout Pro com `expires`/`expiration_date_to` e `date_of_expiration` iguais | A doc limita a vigência da preferência com `expiration_date_to`. Com `date_of_expiration` igual, um Pix gerado não sobrevive ao checkout. |
| Vigência mínima de 30 min no adapter, mesmo com `minutes_to_expire` entre 10 e 29 | A doc diz que o vencimento do Pix fica "entre 30 minutos até 30 dias". O schema de `POST /v1/payments` não muda (10–1440); o adapter estende para 30. |
| Cron `sync_pending_checkouts_worker` a cada 5 min para checkouts do Mercado Pago | O Mercado Pago não notifica expiração de preferência, e a renovação de checkout (commit `0882a17`) depende do status `EXPIRED`. |
| Default de código `DEFAULT_GATEWAY_PROVIDER = mercadopago`, mas o primeiro deploy sobe com `DEFAULT_GATEWAY_PROVIDER=asaas` | Deploy "escuro": o código novo entra em produção sem trocar o gateway. A virada e o rollback passam a ser só uma variável de ambiente. |
| Customers já vinculados ao Asaas continuam no Asaas | Existe a constraint `uq_customers_system_ref`, e `CreateCustomer` devolve o vínculo existente. Levar esses clientes para o Mercado Pago fica fora de escopo. |

## Referências do Mercado Pago (consultadas em 2026-09-14)

- **Webhooks:** o corpo tem `id`, `live_mode`, `type`, `date_created`, `user_id`, `api_version`, `action` e `data.id`.
  - Validação: `x-signature` no formato `ts=<ts>,v1=<hmac>`. O manifest é `id:[data.id_url];request-id:[x-request-id_header];ts:[ts_header];` e o HMAC é SHA256 em hex com a chave secreta da aplicação.
  - `data.id` vem nos query params e deve ir em minúsculas. Um valor ausente sai do manifest.
  - Responder 200/201 em até 22 s; se não, há retentativa a cada 15 min.
- **Tópicos:** `payment`, `subscription_preapproval` (consulta em `/preapproval/search`), `subscription_authorized_payment` (consulta em `https://api.mercadopago.com/authorized_payments/[ID]`).
- **Preapproval pendente:** `POST /preapproval` com `reason`, `external_reference`, `payer_email`, `auto_recurring{frequency, frequency_type, end_date, transaction_amount, currency_id}`, `back_url`, `status: "pending"`.
  - Cancelar ou pausar: `PUT /preapproval/{id}` com `status` `canceled` (texto da doc) ou `paused`. O valor aceito pela API precisa ser confirmado na Task 0.
- **Faturas:**
  - Status `scheduled`, `processed`, `recycling`.
  - A primeira parcela é cobrada cerca de 1 h depois da autorização.
  - Depois de 3 faturas recusadas, a assinatura é cancelada automaticamente.
- **Checkout Pro:**
  - `back_urls{success,pending,failure}` e `auto_return: "approved"`.
  - Vigência: `expires: true`, `expiration_date_from`, `expiration_date_to` (ISO 8601).
- **Pagamentos:**
  - `GET /v1/payments/{id}` devolve `status`, `status_detail`, `date_approved`, `payment_type_id`, `external_reference` e `transaction_details.net_received_amount`.
  - Busca: `GET /v1/payments/search?external_reference=...&sort=date_created&criteria=desc`, com resultado em `results`.
  - Status: `approved`, `authorized`, `in_process`, `pending`, `cancelled`, `charged_back`, `in_mediation`, `refunded`, `rejected`.
- **Customers:** `POST /v1/customers` (`email`, `first_name`, `last_name`, `identification{type: CPF|CNPJ, number}`, `description`) e `GET /v1/customers/search?email=`.
- **Pix:** vence em 24 h por padrão; `date_of_expiration` aceita de 30 min a 30 dias.

**Não confirmado na doc (a Task 0 valida no sandbox antes das Tasks 5–8):**
- (a) A resposta do `POST /preapproval` traz `init_point`.
- (b) Os campos de `GET /authorized_payments/{id}`: `preapproval_id`, `payment.id`, `payment.status`, `debit_date`, `transaction_amount`.
- (c) `GET /authorized_payments/search?preapproval_id=` existe.
- (d) O pagamento de assinatura expõe `point_of_interaction.transaction_data.subscription_id`.
- (e) Na preferência, `date_of_expiration` limita o Pix, e o `payment_type_id` do Pix é `bank_transfer`.
- (f) O `PUT /preapproval/{id}` aceita `status: "cancelled"`.
- (g) O pagador precisa entrar com a conta cujo e-mail é o `payer_email`.

---

## Mapa de arquivos

| Arquivo | Ação | Responsabilidade |
|---|---|---|
| `tests/conftest.py` | Modificar | Env padrão para testes, `FakeMercadoPagoAPI` |
| `app/application/interfaces/gateway_provider.py` | Modificar | `GatewayAPIError`, `resolve_webhook()` padrão |
| `app/infra/interfaces/asaas_provider.py` | Modificar | `AsaasAPIError` herda de `GatewayAPIError` |
| `app/workers/tasks.py` | Modificar | Classificação genérica de erro de gateway, gateway padrão no checkout, `process_gateway_notification`, `sync_pending_checkouts_worker` |
| `app/workers/worker.py` | Modificar | Registrar job e cron novos |
| `app/domain/enums/gateway_provider.py` | Modificar | `MERCADOPAGO` |
| `app/infra/config.py` | Modificar | Variáveis do Mercado Pago e `DEFAULT_GATEWAY_PROVIDER` |
| `.env.example` | Modificar | Variáveis novas |
| `app/web/routes/customers.py` | Modificar | Gateway padrão via settings |
| `app/application/use_cases/create_checkout.py` | Modificar | Renovação troca o gateway do pagamento |
| `app/infra/interfaces/mercadopago_api.py` | Criar | Cliente HTTP e `MercadoPagoAPIError` |
| `app/infra/interfaces/mercadopago_mappers.py` | Criar | Funções puras de tradução |
| `app/infra/interfaces/mercadopago_provider.py` | Criar | `MercadoPagoProvider(InterfaceGateway)` |
| `app/infra/interfaces/mercadopago_signature.py` | Criar | Validação HMAC do `x-signature` |
| `app/infra/interfaces/gateway_provider.py` | Modificar | Registrar `MercadoPagoProvider` |
| `app/web/dependencies/security.py` | Modificar | `validate_mercadopago_webhook` |
| `app/web/routes/webhooks.py` | Modificar | `POST /v1/webhooks/mercadopago` |
| `app/application/use_cases/process_webhook.py` | Modificar | Revincular o pagamento inicial da assinatura |
| `app/application/repositories/payment_repo.py` / `app/infra/repo/payment_repo.py` | Modificar | `list_pending_checkouts` |
| `app/application/use_cases/sync_checkout_status.py` | Criar | Aplicar o status remoto de um checkout pendente |
| `docs/INTEGRATION.md`, `docs/Webhooks.md`, `docs/API.md`, `runbooks/Falha_Gateway.md`, `docs/agent_memory/mercadopago-adapter-contract.md` | Modificar/Criar | Documentação operacional |

---

### Task 0: Ambiente local e spike no sandbox

Não gera código de produção. Deixa a suíte rodável e confirma os pontos (a)–(g) antes de o mapeamento ser escrito.

**Files:**
- Modify: `tests/conftest.py:1-3`
- Create (local, fora do git): `tests/fixtures/mercadopago/*.json`, com payloads reais anonimizados

- [ ] **Step 1: Criar o venv e instalar dependências**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest pytest-asyncio
```

Ative o venv (`source .venv/Scripts/activate` no Git Bash, `.venv\Scripts\Activate.ps1` no PowerShell). Todos os comandos `python -m pytest` deste plano assumem o venv ativo.

- [ ] **Step 2: Rodar a suíte e confirmar que ela não carrega sem env**

Run: `.venv/Scripts/python -m pytest -q`
Expected: FAIL na coleta com `ValidationError ... DATABASE_URL Field required ... ASAAS_API_TOKEN Field required`.

- [ ] **Step 3: Dar valores padrão às variáveis obrigatórias nos testes**

Em `tests/conftest.py`, substituir as linhas 1–3 por:

```python
import os
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://billing:billing@localhost:5432/billing_core_test")
os.environ.setdefault("ASAAS_API_TOKEN", "fake-asaas-api-token")
os.environ["ASAAS_WEBHOOK_SECRET"] = "fake-asaas-webhook-secret-long-enough-32-chars"
os.environ["INTERNAL_WEBHOOK_SIGNATURE"] = "test-webhook-signature-for-dev-only-32-chars"
```

- [ ] **Step 4: Registrar a linha de base**

Run: `.venv/Scripts/python -m pytest -q`
Expected: todos os testes passam. Anote o total (ex.: `N passed`); é a linha de base das próximas tasks. Se algum já falhar, anote no PR e não corrija nesta task.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py
git commit -m "test: default required env vars so the suite runs without a local .env"
```

- [ ] **Step 6: Spike no sandbox do Mercado Pago (manual)**

1. Em "Suas integrações", criar **uma aplicação só para o Billing Core**, separada da aplicação OAuth/PIX do Marketfy.
2. Criar usuários de teste (vendedor e comprador) e pegar o access token `TEST-...` do vendedor.
3. Em Webhooks > Configurar notificações, apontar para a URL de staging `/v1/webhooks/mercadopago`, marcar os eventos **Pagamentos** e **Planos e assinaturas**, e revelar a assinatura secreta.
4. Com `curl`, executar e salvar em `tests/fixtures/mercadopago/` (removendo e-mails e documentos):
   - `POST /checkout/preferences`, usando o payload da Task 6 Step 3, e depois pagar com Pix e com cartão de teste;
   - `GET /v1/payments/{id}` e `GET /v1/payments/search?external_reference=...`;
   - `POST /v1/customers` e `GET /v1/customers/search?email=...`;
   - `POST /preapproval` com `status: "pending"`, usando o payload da Task 7 Step 3, e depois autorizar pelo `init_point` com o comprador de teste;
   - `GET /preapproval/{id}`, `GET /authorized_payments/search?preapproval_id=...` e `GET /authorized_payments/{id}`;
   - `PUT /preapproval/{id}` com `{"status": "cancelled"}`;
   - as notificações recebidas, com os headers `x-signature` e `x-request-id` e a query string.

- [ ] **Step 7: Portão de decisão**

Para cada item (a)–(g) de "Referências", marcar **confirmado** ou **divergente** no PR:
- Se (a), (b) ou (d) divergir, ajustar os nomes de campo **nos dicts de teste e no código** das Tasks 5, 7 e 8 antes de implementá-las.
- Se (c) não existir, `get_subscription_payment` (Task 7) passa a devolver só o pagamento provisório e a Step 4 da Task 7 perde o caso "com faturas".
- Se (f) aceitar só `"canceled"`, trocar a string na Task 7.

---

### Task 1: Erro de gateway genérico nos workers

**Files:**
- Modify: `app/application/interfaces/gateway_provider.py`
- Modify: `app/infra/interfaces/asaas_provider.py:23-32`
- Modify: `app/workers/tasks.py` (imports e os três blocos `except AsaasAPIError`, em `create_subscription_worker`, `create_checkout_worker` e `cancel_subscription_worker`)
- Test: `tests/test_gateway_api_error_handling.py`

**Interfaces:**
- Produces: `GatewayAPIError(status_code: int, body: str, method: str, endpoint: str)`, com o atributo de classe `provider_label: str` e a property `is_client_error -> bool`; `tasks._handle_gateway_api_error(ctx, exc, *, job_name: str, job_id: str, job_try: int, label: str) -> dict` (devolve dict em 4xx e relança em 5xx).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gateway_api_error_handling.py
from types import SimpleNamespace

import pytest

from app.application.interfaces.gateway_provider import GatewayAPIError
from app.infra.interfaces.asaas_provider import AsaasAPIError
from app.workers import tasks


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class OtherGatewayError(GatewayAPIError):
    provider_label = "Other"


class RaisingCheckoutService:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def execute(self, dto, gateway_provider):
        raise self.exc


CHECKOUT_PAYLOAD = {
    "description": "Creditos NF-e - pack_100",
    "value": "72.00",
    "minutes_to_expire": 30,
    "system": "neectify_shop",
    "system_payment_id": "pack-100",
    "webhook_link": "https://hooks.neectify.local/billing/payment",
    "success_url": "https://app.neectify.local/billing/success",
    "cancel_url": "https://app.neectify.local/billing/cancel",
    "expired_url": "https://app.neectify.local/billing/expired",
    "items": [{"external_reference": "pack-100", "name": "100 creditos", "quantity": 1, "value": "72.00"}],
}


def make_ctx(fake_redis):
    return {
        "job_id": "job-gw-1",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }


def patch_checkout_worker(monkeypatch, exc: Exception):
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CreateCheckout", lambda **kwargs: RaisingCheckoutService(exc))


def test_asaas_api_error_keeps_message_and_is_a_gateway_error():
    exc = AsaasAPIError(400, "invalid", "POST", "/checkouts")

    assert isinstance(exc, GatewayAPIError)
    assert str(exc) == "Asaas POST /checkouts → 400: invalid"
    assert exc.is_client_error is True


async def test_checkout_worker_treats_any_gateway_client_error_as_terminal(monkeypatch, fake_redis):
    patch_checkout_worker(monkeypatch, OtherGatewayError(422, "bad request", "POST", "/x"))

    response = await tasks.create_checkout_worker(make_ctx(fake_redis), CHECKOUT_PAYLOAD)

    assert response["status"] == "failed"


async def test_checkout_worker_reraises_gateway_server_error_for_retry(monkeypatch, fake_redis):
    patch_checkout_worker(monkeypatch, OtherGatewayError(503, "unavailable", "POST", "/x"))

    with pytest.raises(OtherGatewayError):
        await tasks.create_checkout_worker(make_ctx(fake_redis), CHECKOUT_PAYLOAD)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_gateway_api_error_handling.py -v`
Expected: FAIL com `ImportError: cannot import name 'GatewayAPIError'`.

- [ ] **Step 3: Implement**

Em `app/application/interfaces/gateway_provider.py`, logo depois dos imports:

```python
class GatewayAPIError(Exception):
    """Erro HTTP devolvido por um gateway. 4xx e terminal; 5xx e transitorio."""

    provider_label = "Gateway"

    def __init__(self, status_code: int, body: str, method: str, endpoint: str) -> None:
        super().__init__(f"{self.provider_label} {method} {endpoint} → {status_code}: {body}")
        self.status_code = status_code
        self.body = body
        self.method = method
        self.endpoint = endpoint

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500
```

Em `app/infra/interfaces/asaas_provider.py`, adicionar `GatewayAPIError` ao import já existente de `app.application.interfaces.gateway_provider` e trocar a classe inteira por:

```python
class AsaasAPIError(GatewayAPIError):
    """Erro retornado pelo Asaas com status HTTP e corpo da resposta."""

    provider_label = "Asaas"
```

Em `app/workers/tasks.py`:
- trocar `from app.infra.interfaces.asaas_provider import AsaasAPIError` por `from app.application.interfaces.gateway_provider import GatewayAPIError`;
- adicionar logo depois de `_persist_internal_delivery`:

```python
async def _handle_gateway_api_error(
    ctx,
    exc: GatewayAPIError,
    *,
    job_name: str,
    job_id: str,
    job_try: int,
    label: str,
) -> dict:
    """Falha 4xx do gateway e terminal (dead letter, sem retry); 5xx volta para o ARQ."""
    error_code = f"{exc.__class__.__name__}_{exc.status_code}"
    log_extra = {"job_id": job_id, "job_try": job_try, "gateway_status": exc.status_code, "gateway_body": exc.body}

    if exc.is_client_error:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=error_code,
            error_message=exc.body[:500],
        )
        await register_dead_letter(ctx["redis"], job_name, job_id)
        ctx["logger"].error(f"{label} failed terminally - gateway returned client error", extra=log_extra)
        return {"status": "failed", "error": str(exc)}

    is_final_try = job_try >= settings.WORKER_MAX_TRIES
    await update_job_metadata(
        ctx["redis"],
        job_id,
        status="failed" if is_final_try else "retrying",
        attempt=job_try,
        finished_at=datetime.now(timezone.utc) if is_final_try else None,
        error_code=error_code,
        error_message=exc.body[:500],
    )
    if is_final_try:
        await register_dead_letter(ctx["redis"], job_name, job_id)
    ctx["logger"].warning(f"{label} transient failure - gateway returned server error, retry scheduled", extra=log_extra)
    raise exc
```

- trocar cada bloco `except AsaasAPIError as exc:` inteiro (até antes do `except Exception`) pela versão correspondente:

```python
    # create_subscription_worker
    except GatewayAPIError as exc:
        return await _handle_gateway_api_error(
            ctx, exc, job_name="create_subscription_worker", job_id=job_id, job_try=job_try, label="Subscription creation"
        )
```

```python
    # create_checkout_worker
    except GatewayAPIError as exc:
        return await _handle_gateway_api_error(
            ctx, exc, job_name="create_checkout_worker", job_id=job_id, job_try=job_try, label="Checkout creation"
        )
```

```python
    # cancel_subscription_worker
    except GatewayAPIError as exc:
        return await _handle_gateway_api_error(
            ctx, exc, job_name="cancel_subscription_worker", job_id=job_id, job_try=job_try, label="Subscription cancellation"
        )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_gateway_api_error_handling.py tests/test_create_checkout_use_case.py tests/test_cancel_subscription_worker.py tests/test_payment_workers.py -v`
Expected: PASS. Depois, `python -m pytest -q`: linha de base + 3.

- [ ] **Step 5: Commit**

```bash
git add app/application/interfaces/gateway_provider.py app/infra/interfaces/asaas_provider.py app/workers/tasks.py tests/test_gateway_api_error_handling.py
git commit -m "refactor(workers): classify gateway HTTP errors independently of the provider"
```

---

### Task 2: Enum e configuração do Mercado Pago

**Files:**
- Modify: `app/domain/enums/gateway_provider.py`
- Modify: `app/infra/config.py`
- Modify: `tests/conftest.py`
- Modify: `.env.example`
- Test: `tests/test_config_gateways.py`

**Interfaces:**
- Produces:
  - `GatewayProvider.MERCADOPAGO` (valor `"mercadopago"`).
  - Em `settings`: `DEFAULT_GATEWAY_PROVIDER: GatewayProvider`, `MERCADOPAGO_ACCESS_TOKEN: str | None`, `MERCADOPAGO_WEBHOOK_SECRET: str | None`, `MERCADOPAGO_BASE_URL: str`, `MERCADOPAGO_SUBSCRIPTION_BACK_URL: str | None`, `MERCADOPAGO_CHECKOUT_EXPIRY_GRACE_SECONDS: int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_gateways.py
import pytest

from app.domain.enums.gateway_provider import GatewayProvider
from app.infra.config import Settings

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "INTERNAL_WEBHOOK_SIGNATURE": "s" * 32,
    "ASAAS_API_TOKEN": "asaas-token",
    "ASAAS_WEBHOOK_SECRET": "a" * 32,
    "MERCADOPAGO_ACCESS_TOKEN": "TEST-123",
    "MERCADOPAGO_WEBHOOK_SECRET": "m" * 64,
    "MERCADOPAGO_SUBSCRIPTION_BACK_URL": "https://app.neectify.com/billing/subscription",
}

PRODUCTION = {
    "APP_ENV": "production",
    "ASAAS_SANDBOX": False,
    "ALLOWED_INTERNAL_WEBHOOK_HOSTS": ["hooks.neectify.com"],
    "INTERNAL_API_CLIENTS": {"marketfy": {"api_key": "k", "scopes": []}},
}


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **(BASE | overrides))


def test_mercadopago_is_the_default_gateway():
    assert make_settings().DEFAULT_GATEWAY_PROVIDER == GatewayProvider.MERCADOPAGO


@pytest.mark.parametrize(
    "missing",
    ["MERCADOPAGO_ACCESS_TOKEN", "MERCADOPAGO_WEBHOOK_SECRET", "MERCADOPAGO_SUBSCRIPTION_BACK_URL"],
)
def test_mercadopago_default_requires_its_credentials(missing):
    with pytest.raises(RuntimeError, match=missing):
        make_settings(**{missing: None}).validate_runtime()


def test_asaas_default_does_not_require_mercadopago_credentials():
    make_settings(
        DEFAULT_GATEWAY_PROVIDER="asaas",
        MERCADOPAGO_ACCESS_TOKEN=None,
        MERCADOPAGO_WEBHOOK_SECRET=None,
        MERCADOPAGO_SUBSCRIPTION_BACK_URL=None,
    ).validate_runtime()


def test_production_rejects_mercadopago_test_token():
    with pytest.raises(RuntimeError, match="TEST-"):
        make_settings(**PRODUCTION).validate_runtime()


def test_production_accepts_mercadopago_live_token():
    make_settings(**(PRODUCTION | {"MERCADOPAGO_ACCESS_TOKEN": "APP_USR-123"})).validate_runtime()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_gateways.py -v`
Expected: FAIL com `AttributeError: MERCADOPAGO` ou `DEFAULT_GATEWAY_PROVIDER` ausente.

- [ ] **Step 3: Implement**

`app/domain/enums/gateway_provider.py`:

```python
from enum import Enum

class GatewayProvider(Enum):
    ASAAS = "asaas"
    MERCADOPAGO = "mercadopago"
```

`app/infra/config.py`:
- adicionar `from app.domain.enums.gateway_provider import GatewayProvider`;
- logo depois de `ASAAS_SANDBOX: bool = True`:

```python
    DEFAULT_GATEWAY_PROVIDER: GatewayProvider = GatewayProvider.MERCADOPAGO
    MERCADOPAGO_ACCESS_TOKEN: str | None = None
    MERCADOPAGO_WEBHOOK_SECRET: str | None = None
    MERCADOPAGO_BASE_URL: str = "https://api.mercadopago.com"
    MERCADOPAGO_SUBSCRIPTION_BACK_URL: str | None = None
    MERCADOPAGO_CHECKOUT_EXPIRY_GRACE_SECONDS: int = 300
```

- no fim de `validate_runtime`, depois do loop de `INTERNAL_API_CLIENTS`:

```python
        if self.DEFAULT_GATEWAY_PROVIDER == GatewayProvider.MERCADOPAGO:
            for required_name in (
                "MERCADOPAGO_ACCESS_TOKEN",
                "MERCADOPAGO_WEBHOOK_SECRET",
                "MERCADOPAGO_SUBSCRIPTION_BACK_URL",
            ):
                if not (getattr(self, required_name) or "").strip():
                    raise RuntimeError(
                        f"Configuracao invalida: {required_name} e obrigatorio quando o gateway padrao e mercadopago."
                    )

        if self.MERCADOPAGO_WEBHOOK_SECRET and any(ch.isspace() for ch in self.MERCADOPAGO_WEBHOOK_SECRET):
            raise RuntimeError("Configuracao invalida: MERCADOPAGO_WEBHOOK_SECRET nao pode conter espacos.")

        if self.is_production and (self.MERCADOPAGO_ACCESS_TOKEN or "").startswith("TEST-"):
            raise RuntimeError("Configuracao invalida: producao nao pode usar access token TEST- do Mercado Pago.")
```

`tests/conftest.py`: logo depois dos `setdefault` da Task 0:

```python
os.environ.setdefault("MERCADOPAGO_ACCESS_TOKEN", "TEST-fake-mercadopago-token")
os.environ.setdefault("MERCADOPAGO_WEBHOOK_SECRET", "fake-mercadopago-webhook-secret-with-32-chars")
os.environ.setdefault("MERCADOPAGO_SUBSCRIPTION_BACK_URL", "https://app.neectify.local/billing/subscription")
```

`.env.example`: logo depois de `ASAAS_SANDBOX=true`:

```
# Gateway usado para customers, checkouts e assinaturas NOVOS: mercadopago | asaas
DEFAULT_GATEWAY_PROVIDER=mercadopago
MERCADOPAGO_ACCESS_TOKEN=TEST-change-me
MERCADOPAGO_WEBHOOK_SECRET=change-me-with-the-secret-from-your-integrations-panel
MERCADOPAGO_BASE_URL=https://api.mercadopago.com
MERCADOPAGO_SUBSCRIPTION_BACK_URL=https://app.neectify.local/billing/subscription
MERCADOPAGO_CHECKOUT_EXPIRY_GRACE_SECONDS=300
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_config_gateways.py -v` e depois `python -m pytest -q`
Expected: PASS em tudo. Um teste de API que monte produção sem credenciais do Mercado Pago (ex.: `test_payment_checkout_does_not_require_redirect_hosts_in_production`) só chama `validate_runtime` se o próprio teste o fizer; se falhar, adicione `monkeypatch.setattr(settings, "MERCADOPAGO_ACCESS_TOKEN", "APP_USR-test")` nesse teste.

- [ ] **Step 5: Commit**

```bash
git add app/domain/enums/gateway_provider.py app/infra/config.py tests/conftest.py tests/test_config_gateways.py .env.example
git commit -m "feat(config): add Mercado Pago settings and default gateway selection"
```

---

### Task 3: Usar o gateway padrão nas criações

**Files:**
- Modify: `app/web/routes/customers.py` (linha 60 e a descrição das linhas 35–36)
- Modify: `app/workers/tasks.py` (`create_checkout_worker`, `service.execute(dto, GatewayProvider.ASAAS)`)
- Modify: `app/application/use_cases/create_checkout.py` (ramo `is_checkout_renewal`)
- Test: `tests/test_create_customer_api.py`, `tests/test_payment_workers.py`, `tests/test_create_checkout_use_case.py`

**Interfaces:**
- Consumes: `settings.DEFAULT_GATEWAY_PROVIDER` (Task 2).
- Produces: a renovação de checkout grava `payment.gateway = gateway_provider`.

- [ ] **Step 1: Write the failing tests**

Adicionar ao fim de `tests/test_create_customer_api.py`:

```python
class _RecordingCreateCustomer:
    def __init__(self):
        self.gateway_provider = None

    async def execute(self, dto, system, gateway_provider):
        self.gateway_provider = gateway_provider
        return "cus_mp_1"


def test_create_customer_uses_default_gateway(fake_redis, monkeypatch):
    from fastapi.testclient import TestClient

    from app.domain.enums.gateway_provider import GatewayProvider
    from app.infra.config import settings

    monkeypatch.setattr(settings, "DEFAULT_GATEWAY_PROVIDER", GatewayProvider.MERCADOPAGO)
    recording = _RecordingCreateCustomer()
    app.dependency_overrides[get_create_customer_use_case] = lambda: recording
    try:
        with TestClient(app) as test_client:
            app.state.redis_pool = fake_redis
            response = test_client.post("/v1/customers", json=_valid_payload(), headers=_auth_headers())
    finally:
        app.dependency_overrides.pop(get_create_customer_use_case, None)

    assert response.status_code == 201
    assert recording.gateway_provider == GatewayProvider.MERCADOPAGO
```

Adicionar ao fim de `tests/test_payment_workers.py`:

```python
class RecordingCreateCheckoutService(FakeCreateCheckoutService):
    provider = None

    async def execute(self, dto, gateway_provider):
        RecordingCreateCheckoutService.provider = gateway_provider
        return await super().execute(dto, gateway_provider)


@pytest.mark.asyncio
async def test_create_checkout_worker_uses_default_gateway(monkeypatch, fake_redis):
    monkeypatch.setattr(tasks.settings, "DEFAULT_GATEWAY_PROVIDER", GatewayProvider.MERCADOPAGO)
    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "GatewayOperationRepositoryINFRA", lambda session: object())
    monkeypatch.setattr(tasks, "UowProvider", lambda session: object())
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "CreateCheckout", lambda **kwargs: RecordingCreateCheckoutService())

    ctx = {
        "job_id": "job-checkout-default",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }
    await tasks.create_checkout_worker(
        ctx,
        {
            "description": "Creditos NF-e - pack_100",
            "value": "72.00",
            "minutes_to_expire": 30,
            "system": "neectify_shop",
            "system_payment_id": "pack-100",
            "webhook_link": "https://hooks.neectify.local/billing/payment",
            "success_url": "https://app.neectify.local/billing/success",
            "cancel_url": "https://app.neectify.local/billing/cancel",
            "expired_url": "https://app.neectify.local/billing/expired",
            "items": [{"external_reference": "pack-100", "name": "100 creditos", "quantity": 1, "value": "72.00"}],
        },
    )

    assert RecordingCreateCheckoutService.provider == GatewayProvider.MERCADOPAGO
```

Adicionar ao fim de `tests/test_create_checkout_use_case.py`:

```python
@pytest.mark.asyncio
async def test_create_checkout_renewal_moves_payment_to_requested_gateway():
    existing = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.ASAAS,
        system_payment_id="order-123",
        provider_payment_id="checkout_expired",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://sandbox.asaas.com/checkoutSession/show/checkout_expired",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference="checkout:marketfy:order-123",
    )
    existing.id = uuid4()
    existing.payment_status = PaymentStatus.EXPIRED
    service = CreateCheckout(
        get_gateway=FakeGetGateway(FakeCheckoutGateway()),
        uow=FakeUow(),
        payment_repo=FakePaymentRepo(existing=existing),
        gateway_operation_repo=FakeGatewayOperationRepo(),
    )

    await service.execute(make_request(), GatewayProvider.MERCADOPAGO)

    assert existing.gateway == GatewayProvider.MERCADOPAGO
    assert existing.payment_status == PaymentStatus.PENDING
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_create_customer_api.py::test_create_customer_uses_default_gateway tests/test_payment_workers.py::test_create_checkout_worker_uses_default_gateway tests/test_create_checkout_use_case.py::test_create_checkout_renewal_moves_payment_to_requested_gateway -v`
Expected: 3 FAIL, com asserts recebendo `GatewayProvider.ASAAS`.

- [ ] **Step 3: Implement**

`app/web/routes/customers.py`:
- adicionar `from app.infra.config import settings`, se ainda não estiver importado;
- remover o import de `GatewayProvider`, se ficar sem uso;
- trocar a linha 60 por `provider_customer_id = await use_case.execute(dto, auth.system, settings.DEFAULT_GATEWAY_PROVIDER)`;
- trocar o texto da descrição por:

```python
        "Registra um novo cliente no gateway de pagamento padrao. "
        "Idempotente por sistema: se o cliente ja tiver vinculo no gateway, retorna o mesmo provider_customer_id."
```

`app/workers/tasks.py`, em `create_checkout_worker`:

```python
            result = await service.execute(dto, settings.DEFAULT_GATEWAY_PROVIDER)
```

`app/application/use_cases/create_checkout.py`, no ramo de renovação, logo depois de `payment.renew_checkout(...)`:

```python
                # O checkout novo nasce no gateway padrao atual; o pagamento acompanha.
                payment.gateway = gateway_provider
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/web/routes/customers.py app/workers/tasks.py app/application/use_cases/create_checkout.py tests/test_create_customer_api.py tests/test_payment_workers.py tests/test_create_checkout_use_case.py
git commit -m "feat: create customers and checkouts on the configured default gateway"
```

---

### Task 4: Cliente HTTP do Mercado Pago

**Files:**
- Create: `app/infra/interfaces/mercadopago_api.py`
- Test: `tests/test_mercadopago_api.py`

**Interfaces:**
- Consumes: `GatewayAPIError` (Task 1).
- Produces:
  - `MercadoPagoAPIError(GatewayAPIError)`.
  - `MercadoPagoAPI(access_token: str, base_url: str, timeout: float = 30.0, transport: httpx.AsyncBaseTransport | None = None)`, com os métodos `async get(endpoint: str, params: dict | None = None) -> dict`, `async post(endpoint: str, payload: dict, idempotency_key: str | None = None) -> dict` e `async put(endpoint: str, payload: dict) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mercadopago_api.py
import httpx
import pytest

from app.infra.interfaces.mercadopago_api import MercadoPagoAPI, MercadoPagoAPIError


async def test_post_sends_bearer_token_and_idempotency_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["idempotency"] = request.headers.get("X-Idempotency-Key")
        seen["url"] = str(request.url)
        return httpx.Response(201, json={"id": "pref-1"})

    api = MercadoPagoAPI("TEST-token", "https://api.mercadopago.com/", transport=httpx.MockTransport(handler))

    body = await api.post("/checkout/preferences", {"items": []}, idempotency_key="checkout:marketfy:1")

    assert body == {"id": "pref-1"}
    assert seen == {
        "authorization": "Bearer TEST-token",
        "idempotency": "checkout:marketfy:1",
        "url": "https://api.mercadopago.com/checkout/preferences",
    }


async def test_get_forwards_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"results": []})

    api = MercadoPagoAPI("TEST-token", "https://api.mercadopago.com", transport=httpx.MockTransport(handler))

    await api.get("/v1/payments/search", params={"external_reference": "checkout:marketfy:1"})

    assert seen["params"] == {"external_reference": "checkout:marketfy:1"}


async def test_error_response_raises_gateway_error_with_status_and_body():
    api = MercadoPagoAPI(
        "TEST-token",
        "https://api.mercadopago.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"message": "invalid"})),
    )

    with pytest.raises(MercadoPagoAPIError) as exc_info:
        await api.put("/preapproval/abc", {"status": "cancelled"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.is_client_error is True
    assert "invalid" in exc_info.value.body
    assert str(exc_info.value).startswith("MercadoPago PUT /preapproval/abc → 400")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mercadopago_api.py -v`
Expected: FAIL com `ModuleNotFoundError: app.infra.interfaces.mercadopago_api`.

- [ ] **Step 3: Implement**

```python
# app/infra/interfaces/mercadopago_api.py
import logging

import httpx

from app.application.interfaces.gateway_provider import GatewayAPIError

logger = logging.getLogger(__name__)


class MercadoPagoAPIError(GatewayAPIError):
    """Erro retornado pela API do Mercado Pago com status HTTP e corpo da resposta."""

    provider_label = "MercadoPago"


class MercadoPagoAPI:
    def __init__(
        self,
        access_token: str,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Neectify/1.0",
        }

    def _build_url(self, endpoint: str) -> str:
        normalized = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        return f"{self.base_url}{normalized}"

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        headers = dict(self.headers)
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key

        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, transport=self.transport) as client:
            response = await client.request(method, self._build_url(endpoint), json=json, params=params)

        if response.is_error:
            logger.error(
                "mercadopago_api_error",
                extra={
                    "extra_data": {
                        "method": method,
                        "endpoint": endpoint,
                        "status_code": response.status_code,
                        "response_body": response.text,
                    }
                },
            )
            raise MercadoPagoAPIError(
                status_code=response.status_code,
                body=response.text,
                method=method,
                endpoint=endpoint,
            )

        return response.json() if response.content else {}

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        return await self._request("GET", endpoint, params=params)

    async def post(self, endpoint: str, payload: dict, idempotency_key: str | None = None) -> dict:
        return await self._request("POST", endpoint, json=payload, idempotency_key=idempotency_key)

    async def put(self, endpoint: str, payload: dict) -> dict:
        return await self._request("PUT", endpoint, json=payload)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mercadopago_api.py -v`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add app/infra/interfaces/mercadopago_api.py tests/test_mercadopago_api.py
git commit -m "feat(mercadopago): add HTTP client with idempotency header and typed errors"
```

---

### Task 5: Tradução dos recursos do Mercado Pago (funções puras)

**Files:**
- Create: `app/infra/interfaces/mercadopago_mappers.py`
- Test: `tests/test_mercadopago_mappers.py`

**Interfaces:**
- Consumes: `WebhookPayload`, `EventType` (`app/application/dtos/request/webhook.py`), `SubscriptionType`.
- Produces:
  - Constantes: `MIN_PIX_EXPIRATION_MINUTES: int = 30`, `FREQUENCY_MONTHS_BY_CYCLE: dict[SubscriptionType, int]`, `CYCLE_BY_FREQUENCY_MONTHS: dict[int, str]`.
  - Datas e status: `parse_datetime(str | None) -> datetime | None`, `payment_status_to_gateway(str | None) -> str`, `preapproval_status_to_gateway(str | None) -> str`.
  - Pagamento: `billing_type_from_payment(dict) -> str`, `excluded_payment_types(list[str]) -> list[dict]`, `net_value(dict) -> Decimal | None`.
  - Checkout: `resolve_checkout_status(preference: dict, payments: list[dict], *, now: datetime, grace: timedelta) -> str`.
  - Webhook: `payment_notification_to_webhook(payment: dict) -> WebhookPayload | None`, `authorized_payment_to_webhook(invoice: dict, payment: dict | None) -> WebhookPayload | None`, `preapproval_notification_to_webhook(preapproval: dict) -> WebhookPayload | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mercadopago_mappers.py
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.application.dtos.request.webhook import EventType
from app.domain.enums.subscription_type import SubscriptionType
from app.infra.interfaces import mercadopago_mappers as m

EXPIRATION = "2026-09-14T12:30:00.000+00:00"
EXPIRES_AT = datetime(2026, 9, 14, 12, 30, tzinfo=timezone.utc)
GRACE = timedelta(minutes=5)


def make_payment(**overrides):
    base = {
        "id": 123456,
        "status": "approved",
        "status_detail": "accredited",
        "date_approved": "2026-09-14T12:10:06.000-03:00",
        "payment_type_id": "bank_transfer",
        "transaction_amount": 72.0,
        "transaction_details": {"net_received_amount": 71.28},
        "external_reference": "checkout:marketfy:order-123",
    }
    return base | overrides


@pytest.mark.parametrize(
    ("status", "expected"),
    [("approved", "RECEIVED"), ("authorized", "CONFIRMED"), ("refunded", "REFUNDED"),
     ("charged_back", "CHARGEBACK_REQUESTED"), ("rejected", "REJECTED"), (None, "")],
)
def test_payment_status_to_gateway(status, expected):
    assert m.payment_status_to_gateway(status) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [("authorized", "ACTIVE"), ("pending", "PENDING"), ("paused", "PAUSED"), ("cancelled", "CANCELED")],
)
def test_preapproval_status_to_gateway(status, expected):
    assert m.preapproval_status_to_gateway(status) == expected


def test_cycles_map_to_months_and_back():
    assert m.FREQUENCY_MONTHS_BY_CYCLE[SubscriptionType.SEMIANNUAL] == 6
    assert m.CYCLE_BY_FREQUENCY_MONTHS[12] == "YEARLY"


def test_excluded_payment_types_keeps_only_requested_billing_types():
    excluded = m.excluded_payment_types(["PIX", "CREDIT_CARD"])

    assert {"id": "bank_transfer"} not in excluded
    assert {"id": "credit_card"} not in excluded
    assert {"id": "ticket"} in excluded
    assert {"id": "debit_card"} in excluded


def test_billing_type_and_net_value_from_payment():
    payment = make_payment()

    assert m.billing_type_from_payment(payment) == "PIX"
    assert m.billing_type_from_payment(make_payment(payment_type_id="account_money")) == "UNDEFINED"
    assert m.net_value(payment) == Decimal("71.28")


@pytest.mark.parametrize(
    ("payments", "now", "expected"),
    [
        ([make_payment(status="approved")], EXPIRES_AT + timedelta(hours=1), "PAID"),
        ([], EXPIRES_AT - timedelta(minutes=1), "ACTIVE"),
        ([], EXPIRES_AT + timedelta(minutes=4), "ACTIVE"),
        ([], EXPIRES_AT + timedelta(minutes=6), "EXPIRED"),
        ([make_payment(status="cancelled")], EXPIRES_AT + timedelta(minutes=6), "EXPIRED"),
        ([make_payment(status="in_process")], EXPIRES_AT + timedelta(minutes=6), "ACTIVE"),
    ],
)
def test_resolve_checkout_status(payments, now, expected):
    preference = {"id": "pref-1", "expires": True, "expiration_date_to": EXPIRATION}

    assert m.resolve_checkout_status(preference, payments, now=now, grace=GRACE) == expected


def test_resolve_checkout_status_without_expiration_stays_active():
    assert m.resolve_checkout_status({"expires": False}, [], now=EXPIRES_AT + timedelta(days=9), grace=GRACE) == "ACTIVE"


def test_approved_checkout_payment_becomes_checkout_paid():
    payload = m.payment_notification_to_webhook(make_payment())

    assert payload.event == EventType.CHECKOUT_PAID
    assert payload.source_event_id == "payment:123456:approved"
    assert payload.details.id == "123456"
    assert payload.details.subscription is None
    assert payload.details.external_reference == "checkout:marketfy:order-123"
    assert payload.details.value == Decimal("72.0")
    assert payload.details.net_value == Decimal("71.28")
    assert payload.details.payment_date == datetime(2026, 9, 14, 15, 10, 6, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("status", "event"),
    [("refunded", EventType.PAYMENT_REFUNDED), ("charged_back", EventType.PAYMENT_CHARGEBACK_REQUESTED)],
)
def test_checkout_payment_reversal_maps_to_payment_event(status, event):
    payload = m.payment_notification_to_webhook(make_payment(status=status))

    assert payload.event == event
    assert payload.details.status == m.payment_status_to_gateway(status)


@pytest.mark.parametrize("status", ["pending", "rejected", "cancelled", "in_process"])
def test_non_final_checkout_payment_is_ignored(status):
    assert m.payment_notification_to_webhook(make_payment(status=status)) is None


def test_subscription_payment_refund_uses_subscription_id_from_payment():
    payment = make_payment(
        status="refunded",
        external_reference="sub_marketfy_1",
        point_of_interaction={"transaction_data": {"subscription_id": "2c938084726fca48"}},
    )

    payload = m.payment_notification_to_webhook(payment)

    assert payload.event == EventType.PAYMENT_REFUNDED
    assert payload.details.subscription == "2c938084726fca48"


def test_subscription_payment_approval_via_payment_topic_is_ignored():
    payment = make_payment(external_reference="sub_marketfy_1", payment_type_id="credit_card")

    assert m.payment_notification_to_webhook(payment) is None


def test_approved_authorized_payment_becomes_payment_received():
    invoice = {"id": 7001, "preapproval_id": "2c938084726fca48", "status": "processed", "payment": {"id": 123456, "status": "approved"}}

    payload = m.authorized_payment_to_webhook(invoice, make_payment(payment_type_id="credit_card", external_reference="sub_marketfy_1"))

    assert payload.event == EventType.PAYMENT_RECEIVED
    assert payload.source_event_id == "authorized_payment:7001:approved"
    assert payload.details.id == "123456"
    assert payload.details.subscription == "2c938084726fca48"
    assert payload.details.billing_type == "CREDIT_CARD"
    assert payload.details.status == "RECEIVED"


def test_authorized_payment_without_payment_or_with_rejection_is_ignored():
    invoice = {"id": 7001, "preapproval_id": "2c938084726fca48", "status": "recycling"}

    assert m.authorized_payment_to_webhook(invoice, None) is None
    assert m.authorized_payment_to_webhook(invoice, make_payment(status="rejected")) is None


def test_cancelled_preapproval_becomes_subscription_inactivated():
    payload = m.preapproval_notification_to_webhook({"id": "2c938084726fca48", "status": "cancelled"})

    assert payload.event == EventType.SUBSCRIPTION_INACTIVATED
    assert payload.source_event_id == "preapproval:2c938084726fca48:cancelled"
    assert payload.details.subscription == "2c938084726fca48"


@pytest.mark.parametrize("status", ["pending", "authorized", "paused"])
def test_other_preapproval_statuses_are_ignored(status):
    assert m.preapproval_notification_to_webhook({"id": "2c938084726fca48", "status": status}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mercadopago_mappers.py -v`
Expected: FAIL com `ImportError: cannot import name 'mercadopago_mappers'`.

- [ ] **Step 3: Implement**

```python
# app/infra/interfaces/mercadopago_mappers.py
"""Traducao dos recursos do Mercado Pago para o vocabulario que os use cases ja usam.

Reconciliacao, cancelamento e processamento de webhook comparam os status do Asaas
(ACTIVE, RECEIVED, CONFIRMED, CHECKOUT_PAID...). O adapter do Mercado Pago entrega
esses mesmos valores para que nada acima dele precise mudar.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from app.application.dtos.request.webhook import EventType, WebhookPayload
from app.domain.enums.subscription_type import SubscriptionType

CHECKOUT_REFERENCE_PREFIX = "checkout:"
# Vencimento minimo aceito pelo Pix no Mercado Pago.
MIN_PIX_EXPIRATION_MINUTES = 30

FREQUENCY_MONTHS_BY_CYCLE = {
    SubscriptionType.MONTHLY: 1,
    SubscriptionType.SEMIANNUAL: 6,
    SubscriptionType.YEARLY: 12,
}
CYCLE_BY_FREQUENCY_MONTHS = {months: cycle.value for cycle, months in FREQUENCY_MONTHS_BY_CYCLE.items()}

BILLING_TYPE_BY_PAYMENT_TYPE_ID = {
    "credit_card": "CREDIT_CARD",
    "debit_card": "DEBIT_CARD",
    "bank_transfer": "PIX",
    "ticket": "BOLETO",
}
PAYMENT_TYPE_ID_BY_BILLING_TYPE = {value: key for key, value in BILLING_TYPE_BY_PAYMENT_TYPE_ID.items()}
# Tipos que o Checkout Pro pode oferecer; ficam disponiveis so quando pedidos em billing_types.
EXCLUDABLE_PAYMENT_TYPE_IDS = ("credit_card", "debit_card", "bank_transfer", "ticket", "atm", "prepaid_card")

_PAYMENT_STATUS_TO_GATEWAY = {
    "approved": "RECEIVED",
    "authorized": "CONFIRMED",
    "refunded": "REFUNDED",
    "charged_back": "CHARGEBACK_REQUESTED",
}
_PREAPPROVAL_STATUS_TO_GATEWAY = {
    "authorized": "ACTIVE",
    "pending": "PENDING",
    "paused": "PAUSED",
    "cancelled": "CANCELED",
}
_REVERSAL_EVENTS = {
    "refunded": EventType.PAYMENT_REFUNDED,
    "charged_back": EventType.PAYMENT_CHARGEBACK_REQUESTED,
}
_UNSETTLED_PAYMENT_STATUSES = {"pending", "in_process", "authorized", "in_mediation"}


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def payment_status_to_gateway(status: str | None) -> str:
    normalized = (status or "").lower()
    return _PAYMENT_STATUS_TO_GATEWAY.get(normalized, normalized.upper())


def preapproval_status_to_gateway(status: str | None) -> str:
    normalized = (status or "").lower()
    return _PREAPPROVAL_STATUS_TO_GATEWAY.get(normalized, normalized.upper())


def billing_type_from_payment(payment: dict) -> str:
    return BILLING_TYPE_BY_PAYMENT_TYPE_ID.get(payment.get("payment_type_id") or "", "UNDEFINED")


def excluded_payment_types(billing_types: list[str]) -> list[dict]:
    allowed = {PAYMENT_TYPE_ID_BY_BILLING_TYPE[item] for item in billing_types if item in PAYMENT_TYPE_ID_BY_BILLING_TYPE}
    return [{"id": type_id} for type_id in EXCLUDABLE_PAYMENT_TYPE_IDS if type_id not in allowed]


def _decimal(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def net_value(payment: dict) -> Decimal | None:
    return _decimal((payment.get("transaction_details") or {}).get("net_received_amount"))


def resolve_checkout_status(preference: dict, payments: list[dict], *, now: datetime, grace: timedelta) -> str:
    """Preferencia nao tem status: deriva ACTIVE/PAID/EXPIRED dos pagamentos e da vigencia."""
    statuses = {(payment.get("status") or "").lower() for payment in payments}
    if "approved" in statuses:
        return "PAID"

    expiration = parse_datetime(preference.get("expiration_date_to"))
    if not preference.get("expires") or expiration is None or now <= expiration + grace:
        return "ACTIVE"

    # Pagamento em analise ainda pode aprovar depois do prazo: so expira apos o desfecho.
    if statuses & _UNSETTLED_PAYMENT_STATUSES:
        return "ACTIVE"

    return "EXPIRED"


def _payment_details(payment: dict, *, subscription: str | None, status: str) -> dict:
    return {
        "id": str(payment["id"]),
        "subscription": subscription,
        "status": status,
        "value": _decimal(payment.get("transaction_amount")),
        "net_value": net_value(payment),
        "payment_date": payment.get("date_approved"),
        "external_reference": payment.get("external_reference"),
        "billing_type": billing_type_from_payment(payment),
    }


def subscription_id_from_payment(payment: dict) -> str | None:
    transaction_data = (payment.get("point_of_interaction") or {}).get("transaction_data") or {}
    return transaction_data.get("subscription_id")


def payment_notification_to_webhook(payment: dict) -> WebhookPayload | None:
    status = (payment.get("status") or "").lower()
    source_event_id = f"payment:{payment['id']}:{status}"
    external_reference = payment.get("external_reference") or ""

    if external_reference.startswith(CHECKOUT_REFERENCE_PREFIX):
        if status == "approved":
            event = EventType.CHECKOUT_PAID
            details = _payment_details(payment, subscription=None, status="PAID")
        elif status in _REVERSAL_EVENTS:
            event = _REVERSAL_EVENTS[status]
            details = _payment_details(payment, subscription=None, status=payment_status_to_gateway(status))
        else:
            # Recusa ou Pix vencido nao encerram o checkout: o comprador pode tentar de novo.
            return None
        return WebhookPayload.model_validate({"event": event, "source_event_id": source_event_id, "details": details})

    subscription_id = subscription_id_from_payment(payment)
    if status in _REVERSAL_EVENTS and subscription_id:
        return WebhookPayload.model_validate(
            {
                "event": _REVERSAL_EVENTS[status],
                "source_event_id": source_event_id,
                "details": _payment_details(payment, subscription=subscription_id, status=payment_status_to_gateway(status)),
            }
        )

    # Aprovacoes de assinatura chegam pelo topico subscription_authorized_payment.
    return None


def authorized_payment_to_webhook(invoice: dict, payment: dict | None) -> WebhookPayload | None:
    if not payment:
        return None

    status = (payment.get("status") or "").lower()
    if status == "approved":
        event = EventType.PAYMENT_RECEIVED
    elif status in _REVERSAL_EVENTS:
        event = _REVERSAL_EVENTS[status]
    else:
        return None

    return WebhookPayload.model_validate(
        {
            "event": event,
            "source_event_id": f"authorized_payment:{invoice['id']}:{status}",
            "details": _payment_details(
                payment,
                subscription=invoice.get("preapproval_id"),
                status=payment_status_to_gateway(status),
            ),
        }
    )


def preapproval_notification_to_webhook(preapproval: dict) -> WebhookPayload | None:
    if (preapproval.get("status") or "").lower() != "cancelled":
        return None

    preapproval_id = str(preapproval["id"])
    return WebhookPayload.model_validate(
        {
            "event": EventType.SUBSCRIPTION_INACTIVATED,
            "source_event_id": f"preapproval:{preapproval_id}:cancelled",
            "details": {"id": preapproval_id, "subscription": preapproval_id, "status": "CANCELED"},
        }
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mercadopago_mappers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/infra/interfaces/mercadopago_mappers.py tests/test_mercadopago_mappers.py
git commit -m "feat(mercadopago): map payments, invoices and preapprovals to gateway vocabulary"
```

---

### Task 6: Provider do Mercado Pago — customers, checkout e pagamento

**Files:**
- Create: `app/infra/interfaces/mercadopago_provider.py`
- Modify: `tests/conftest.py` (adicionar `FakeMercadoPagoAPI` e a fixture `fake_mp_api`)
- Test: `tests/test_mercadopago_provider_checkout.py`

**Interfaces:**
- Consumes: `MercadoPagoAPI` (Task 4), mappers (Task 5), `settings.MERCADOPAGO_*` (Task 2).
- Produces: `MercadoPagoProvider(api: MercadoPagoAPI | None = None, *, now: Callable[[], datetime] | None = None)`, com `create_customer`, `get_customer`, `create_checkout`, `get_checkout` e `get_payment`, todos com as assinaturas da `InterfaceGateway`. Os métodos de assinatura e webhook entram nas Tasks 7 e 8; nesta task eles levantam `NotImplementedError` para a classe poder ser instanciada.

- [ ] **Step 1: Write the failing test**

Adicionar ao fim de `tests/conftest.py`:

```python
class FakeMercadoPagoAPI:
    """Substitui MercadoPagoAPI: responde por (metodo, endpoint) e registra as chamadas."""

    def __init__(self, responses: dict[tuple[str, str], dict]):
        self.responses = responses
        self.calls: list[dict] = []

    def _respond(self, method: str, endpoint: str, **call):
        self.calls.append({"method": method, "endpoint": endpoint, **call})
        return self.responses[(method, endpoint)]

    async def get(self, endpoint, params=None):
        return self._respond("GET", endpoint, params=params)

    async def post(self, endpoint, payload, idempotency_key=None):
        return self._respond("POST", endpoint, payload=payload, idempotency_key=idempotency_key)

    async def put(self, endpoint, payload):
        return self._respond("PUT", endpoint, payload=payload)


@pytest.fixture
def fake_mp_api():
    return FakeMercadoPagoAPI
```

```python
# tests/test_mercadopago_provider_checkout.py
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.errors import DomainError
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
REFERENCE = "checkout:marketfy:order-123"
INIT_POINT = "https://www.mercadopago.com.br/checkout/v1/redirect?pref_id=pref-1"
CALLBACK = {"successUrl": "https://app.test/s", "cancelUrl": "https://app.test/c", "expiredUrl": "https://app.test/e"}
ITEMS = [{"externalReference": "pack-100", "name": "100 creditos", "description": "", "quantity": 1, "value": 72.0}]


def make_provider(api, now=NOW):
    return MercadoPagoProvider(api=api, now=lambda: now)


async def create_checkout(provider, minutes_to_expire=45):
    return await provider.create_checkout(
        billing_types=["PIX", "CREDIT_CARD"],
        charge_types=["DETACHED"],
        minutes_to_expire=minutes_to_expire,
        external_reference=REFERENCE,
        callback=CALLBACK,
        items=ITEMS,
    )


async def test_create_checkout_builds_preference_limited_to_pix_and_card(fake_mp_api):
    api = fake_mp_api({("POST", "/checkout/preferences"): {"id": "pref-1", "init_point": INIT_POINT, "external_reference": REFERENCE}})

    response = await create_checkout(make_provider(api))

    call = api.calls[0]
    payload = call["payload"]
    assert call["idempotency_key"] == REFERENCE
    assert payload["external_reference"] == REFERENCE
    assert payload["items"] == [
        {"id": "pack-100", "title": "100 creditos", "description": "", "quantity": 1, "unit_price": 72.0, "currency_id": "BRL"}
    ]
    assert payload["back_urls"] == {"success": "https://app.test/s", "pending": "https://app.test/s", "failure": "https://app.test/c"}
    assert payload["auto_return"] == "approved"
    assert payload["expires"] is True
    assert payload["expiration_date_from"] == "2026-09-14T12:00:00.000+00:00"
    assert payload["expiration_date_to"] == "2026-09-14T12:45:00.000+00:00"
    assert payload["date_of_expiration"] == payload["expiration_date_to"]
    excluded = payload["payment_methods"]["excluded_payment_types"]
    assert {"id": "bank_transfer"} not in excluded
    assert {"id": "ticket"} in excluded
    assert response.checkout_id == "pref-1"
    assert response.checkout_url == INIT_POINT
    assert response.status == "ACTIVE"
    assert response.external_reference == REFERENCE


async def test_create_checkout_extends_short_expiration_to_pix_minimum(fake_mp_api):
    api = fake_mp_api({("POST", "/checkout/preferences"): {"id": "pref-1", "init_point": INIT_POINT, "external_reference": REFERENCE}})

    await create_checkout(make_provider(api), minutes_to_expire=10)

    assert api.calls[0]["payload"]["expiration_date_to"] == "2026-09-14T12:30:00.000+00:00"


@pytest.mark.parametrize(
    "response",
    [
        {"init_point": INIT_POINT, "external_reference": REFERENCE},
        {"id": "pref-1", "external_reference": REFERENCE},
        {"id": "pref-1", "init_point": INIT_POINT},
        {"id": "pref-1", "init_point": INIT_POINT, "external_reference": "checkout:marketfy:other"},
    ],
)
async def test_create_checkout_rejects_incomplete_or_divergent_response(fake_mp_api, response):
    api = fake_mp_api({("POST", "/checkout/preferences"): response})

    with pytest.raises(DomainError):
        await create_checkout(make_provider(api))


@pytest.mark.parametrize(
    ("results", "now", "expected"),
    [
        ([{"id": 1, "status": "approved"}], NOW, "PAID"),
        ([], NOW, "ACTIVE"),
        ([], NOW + timedelta(hours=1), "EXPIRED"),
    ],
)
async def test_get_checkout_derives_status_from_payments(fake_mp_api, results, now, expected):
    api = fake_mp_api(
        {
            ("GET", "/checkout/preferences/pref-1"): {
                "id": "pref-1",
                "init_point": INIT_POINT,
                "external_reference": REFERENCE,
                "expires": True,
                "expiration_date_to": "2026-09-14T12:30:00.000+00:00",
            },
            ("GET", "/v1/payments/search"): {"paging": {"total": len(results)}, "results": results},
        }
    )

    response = await make_provider(api, now=now).get_checkout("pref-1")

    assert response.status == expected
    assert api.calls[1]["params"] == {"external_reference": REFERENCE, "sort": "date_created", "criteria": "desc"}


async def test_get_checkout_rejects_other_preference(fake_mp_api):
    api = fake_mp_api({("GET", "/checkout/preferences/pref-1"): {"id": "pref-2", "init_point": INIT_POINT, "external_reference": REFERENCE}})

    with pytest.raises(DomainError, match="id divergente"):
        await make_provider(api).get_checkout("pref-1")


async def test_get_payment_maps_status_net_value_and_billing_type(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/payments/123"): {
                "id": 123,
                "status": "approved",
                "transaction_amount": 72.0,
                "transaction_details": {"net_received_amount": 71.28},
                "date_approved": "2026-09-14T12:10:06.000-03:00",
                "payment_type_id": "credit_card",
                "external_reference": REFERENCE,
            }
        }
    )

    response = await make_provider(api).get_payment("123")

    assert response.payment_id == "123"
    assert response.status == "RECEIVED"
    assert response.value == Decimal("72.0")
    assert response.net_value == Decimal("71.28")
    assert response.billing_type == "CREDIT_CARD"
    assert str(response.payment_date) == "2026-09-14"


async def test_create_customer_reuses_customer_found_by_email(fake_mp_api):
    api = fake_mp_api(
        {("GET", "/v1/customers/search"): {"results": [{"id": "1234-abc", "email": "joao@exemplo.com", "first_name": "Joao", "last_name": "Silva"}]}}
    )

    response = await make_provider(api).create_customer(name="Joao Silva", cpfCnpj="39053344705", email="joao@exemplo.com", external_reference="user_42")

    assert response.cus_id == "1234-abc"
    assert [call["method"] for call in api.calls] == ["GET"]


async def test_create_customer_creates_with_document_identification(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/customers/search"): {"results": []},
            ("POST", "/v1/customers"): {"id": "5678-def", "email": "empresa@exemplo.com", "first_name": "Empresa", "last_name": "LTDA"},
        }
    )

    response = await make_provider(api).create_customer(name="Empresa LTDA", cpfCnpj="11222333000181", email="empresa@exemplo.com", external_reference="user_7")

    assert api.calls[1]["payload"] == {
        "email": "empresa@exemplo.com",
        "first_name": "Empresa",
        "last_name": "LTDA",
        "identification": {"type": "CNPJ", "number": "11222333000181"},
        "description": "user_7",
    }
    assert response.cus_id == "5678-def"
    assert response.external_reference == "user_7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mercadopago_provider_checkout.py -v`
Expected: FAIL com `ModuleNotFoundError: app.infra.interfaces.mercadopago_provider`.

- [ ] **Step 3: Implement**

```python
# app/infra/interfaces/mercadopago_provider.py
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.application.dtos.request.webhook import WebhookPayload
from app.application.interfaces.gateway_provider import (
    CreateCheckoutGatewayResponse,
    GetCustomerResponse,
    InterfaceGateway,
    PaymentStatusGatewayResponse,
    SubscriptionPaymentResponse,
    SubscriptionStatusResponse,
)
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.errors import DomainError
from app.infra.config import settings
from app.infra.interfaces.mercadopago_api import MercadoPagoAPI
from app.infra.interfaces.mercadopago_mappers import (
    MIN_PIX_EXPIRATION_MINUTES,
    billing_type_from_payment,
    excluded_payment_types,
    net_value,
    parse_datetime,
    payment_status_to_gateway,
    resolve_checkout_status,
)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


class MercadoPagoProvider(InterfaceGateway):
    def __init__(self, api: MercadoPagoAPI | None = None, *, now: Callable[[], datetime] | None = None):
        self.api = api or MercadoPagoAPI(settings.MERCADOPAGO_ACCESS_TOKEN or "", settings.MERCADOPAGO_BASE_URL)
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ----------------------------------------------------------------- customers

    @staticmethod
    def _customer_response(data: dict, *, name: str, email: str, external_reference: str | None) -> GetCustomerResponse:
        full_name = " ".join(part for part in (data.get("first_name"), data.get("last_name")) if part)
        return GetCustomerResponse(
            cus_id=str(data["id"]),
            name=full_name or name,
            email=data.get("email") or email,
            external_reference=data.get("description") or external_reference,
            deleted=False,
        )

    async def create_customer(self, name: str, cpfCnpj: str, email: str, external_reference: str) -> GetCustomerResponse:
        found = await self.api.get("/v1/customers/search", params={"email": email})
        results = found.get("results") or []
        if results:
            return self._customer_response(results[0], name=name, email=email, external_reference=external_reference)

        first_name, _, last_name = name.strip().partition(" ")
        created = await self.api.post(
            "/v1/customers",
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "identification": {"type": "CPF" if len(cpfCnpj) == 11 else "CNPJ", "number": cpfCnpj},
                "description": external_reference,
            },
        )
        return self._customer_response(created, name=name, email=email, external_reference=external_reference)

    async def get_customer(self, cus_id: str) -> GetCustomerResponse | None:
        data = await self.api.get(f"/v1/customers/{cus_id}")
        return self._customer_response(data, name="", email="", external_reference=None)

    # ------------------------------------------------------------------ checkout

    @staticmethod
    def _checkout_response(preference: dict, *, status: str) -> CreateCheckoutGatewayResponse:
        checkout_id = preference.get("id")
        checkout_url = preference.get("init_point")
        external_reference = preference.get("external_reference")
        if not checkout_id or not checkout_url or not external_reference:
            raise DomainError("Resposta de checkout do Mercado Pago incompleta.")
        return CreateCheckoutGatewayResponse(
            checkout_id=str(checkout_id),
            checkout_url=checkout_url,
            status=status,
            external_reference=external_reference,
        )

    async def create_checkout(
        self,
        *,
        billing_types: list[str],
        charge_types: list[str],
        minutes_to_expire: int,
        external_reference: str,
        callback: dict,
        items: list[dict],
    ) -> CreateCheckoutGatewayResponse:
        now = self._now()
        # Pix gerado vence junto com o checkout; o Pix exige ao menos 30 minutos.
        expires_at = _iso(now + timedelta(minutes=max(minutes_to_expire, MIN_PIX_EXPIRATION_MINUTES)))
        payload = {
            "external_reference": external_reference,
            "items": [
                {
                    "id": item["externalReference"],
                    "title": item["name"],
                    "description": item.get("description") or "",
                    "quantity": item["quantity"],
                    "unit_price": item["value"],
                    "currency_id": "BRL",
                }
                for item in items
            ],
            "back_urls": {
                "success": callback.get("successUrl"),
                "pending": callback.get("successUrl"),
                "failure": callback.get("cancelUrl"),
            },
            "auto_return": "approved",
            "expires": True,
            "expiration_date_from": _iso(now),
            "expiration_date_to": expires_at,
            "date_of_expiration": expires_at,
            "payment_methods": {"excluded_payment_types": excluded_payment_types(billing_types)},
        }
        response = await self.api.post("/checkout/preferences", payload, idempotency_key=external_reference)
        checkout = self._checkout_response(response, status="ACTIVE")
        if checkout.external_reference != external_reference:
            raise DomainError("Checkout do Mercado Pago retornou external_reference divergente.")
        return checkout

    async def get_checkout(self, checkout_id: str) -> CreateCheckoutGatewayResponse:
        preference = await self.api.get(f"/checkout/preferences/{checkout_id}")
        if str(preference.get("id")) != checkout_id:
            raise DomainError("Checkout do Mercado Pago retornou id divergente.")

        payments: list[dict] = []
        if preference.get("external_reference"):
            search = await self.api.get(
                "/v1/payments/search",
                params={"external_reference": preference["external_reference"], "sort": "date_created", "criteria": "desc"},
            )
            payments = search.get("results") or []

        status = resolve_checkout_status(
            preference,
            payments,
            now=self._now(),
            grace=timedelta(seconds=settings.MERCADOPAGO_CHECKOUT_EXPIRY_GRACE_SECONDS),
        )
        return self._checkout_response(preference, status=status)

    async def get_payment(self, payment_id: str) -> PaymentStatusGatewayResponse:
        payment = await self.api.get(f"/v1/payments/{payment_id}")
        approved_at = parse_datetime(payment.get("date_approved"))
        return PaymentStatusGatewayResponse(
            payment_id=str(payment["id"]),
            status=payment_status_to_gateway(payment.get("status")),
            value=Decimal(str(payment["transaction_amount"])),
            net_value=net_value(payment),
            due_date=None,
            payment_date=approved_at.date() if approved_at else None,
            invoice_url=None,
            billing_type=billing_type_from_payment(payment),
            external_reference=payment.get("external_reference"),
        )

    # ------------------------------------------------ assinaturas e webhook (Tasks 7-8)

    def normalize_webhook(self, payload: dict) -> WebhookPayload:
        raise NotImplementedError

    async def create_subscription(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        next_due_date: date,
        cycle: SubscriptionType,
        description: str,
        external_reference: str | None = None,
    ) -> str:
        raise NotImplementedError

    async def get_subscription_payment(self, subscription_id: str) -> list[SubscriptionPaymentResponse]:
        raise NotImplementedError

    async def cancel_subscription(self, subscription_id: str) -> str:
        raise NotImplementedError

    async def verify_status(self, subscription_id: str) -> SubscriptionStatusResponse:
        raise NotImplementedError
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mercadopago_provider_checkout.py -v` e depois `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/infra/interfaces/mercadopago_provider.py tests/conftest.py tests/test_mercadopago_provider_checkout.py
git commit -m "feat(mercadopago): customers, Checkout Pro preferences and payment lookup"
```

---

### Task 7: Provider do Mercado Pago — assinaturas e registro no factory

**Files:**
- Modify: `app/infra/interfaces/mercadopago_provider.py` (substituir os quatro `NotImplementedError` de assinatura)
- Modify: `app/infra/interfaces/gateway_provider.py`
- Test: `tests/test_mercadopago_provider_subscription.py`

**Interfaces:**
- Consumes: `FREQUENCY_MONTHS_BY_CYCLE`, `CYCLE_BY_FREQUENCY_MONTHS`, `preapproval_status_to_gateway`, `payment_status_to_gateway` (Task 5); `settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL`.
- Produces:
  - `create_subscription` devolve o id do preapproval.
  - `get_subscription_payment` devolve as faturas pagas ou, sem faturas, **um** `SubscriptionPaymentResponse(payment_id=<id do preapproval>, status="PENDING", invoice_url=<init_point>)`. A Task 10 depende desse contrato.
  - `verify_status` usa os status `ACTIVE`/`PENDING`/`PAUSED`/`CANCELED`.
  - `GetGatewayInfra().get(GatewayProvider.MERCADOPAGO)` devolve `MercadoPagoProvider`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mercadopago_provider_subscription.py
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_type import PaymentType
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.errors import DomainError
from app.infra.config import settings
from app.infra.interfaces.gateway_provider import GetGatewayInfra
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
PREAPPROVAL_ID = "2c938084726fca480172750000000000"
INIT_POINT = f"https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id={PREAPPROVAL_ID}"


def make_provider(api):
    return MercadoPagoProvider(api=api, now=lambda: NOW)


def preapproval(**overrides):
    base = {
        "id": PREAPPROVAL_ID,
        "status": "pending",
        "init_point": INIT_POINT,
        "next_payment_date": "2026-10-01T12:00:00.000-03:00",
        "auto_recurring": {"frequency": 6, "frequency_type": "months", "transaction_amount": 129.9, "currency_id": "BRL"},
    }
    return base | overrides


async def test_create_subscription_posts_pending_preapproval_for_customer_email(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/v1/customers/cus-1"): {"id": "cus-1", "email": "joao@exemplo.com"},
            ("POST", "/preapproval"): preapproval(),
        }
    )

    subscription_id = await make_provider(api).create_subscription(
        customer_provider_id="cus-1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=date(2026, 10, 1),
        cycle=SubscriptionType.SEMIANNUAL,
        description="Plano Pro",
        external_reference="sub_marketfy_1",
    )

    post = api.calls[1]
    assert subscription_id == PREAPPROVAL_ID
    assert post["idempotency_key"] == "preapproval:sub_marketfy_1"
    assert post["payload"] == {
        "reason": "Plano Pro",
        "external_reference": "sub_marketfy_1",
        "payer_email": "joao@exemplo.com",
        "auto_recurring": {
            "frequency": 6,
            "frequency_type": "months",
            "transaction_amount": 129.9,
            "currency_id": "BRL",
            "start_date": "2026-10-01T00:00:00.000+00:00",
        },
        "back_url": settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL,
        "status": "pending",
    }


async def test_create_subscription_starting_today_omits_start_date(fake_mp_api):
    api = fake_mp_api({("GET", "/v1/customers/cus-1"): {"id": "cus-1", "email": "joao@exemplo.com"}, ("POST", "/preapproval"): preapproval()})

    await make_provider(api).create_subscription(
        customer_provider_id="cus-1",
        billing_type=PaymentType.CREDIT_CARD,
        value=Decimal("129.90"),
        next_due_date=NOW.date(),
        cycle=SubscriptionType.MONTHLY,
        description="Plano Pro",
        external_reference="sub_marketfy_1",
    )

    assert "start_date" not in api.calls[1]["payload"]["auto_recurring"]
    assert api.calls[1]["payload"]["auto_recurring"]["frequency"] == 1


@pytest.mark.parametrize("billing_type", [PaymentType.PIX, PaymentType.BOLETO, PaymentType.DEBIT_CARD])
async def test_create_subscription_rejects_non_credit_card(fake_mp_api, billing_type):
    with pytest.raises(DomainError):
        await make_provider(fake_mp_api({})).create_subscription(
            customer_provider_id="cus-1",
            billing_type=billing_type,
            value=Decimal("10"),
            next_due_date=NOW.date(),
            cycle=SubscriptionType.MONTHLY,
            description="Plano",
        )


async def test_subscription_without_invoices_returns_provisional_payment_with_init_point(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(),
            ("GET", "/authorized_payments/search"): {"results": []},
        }
    )

    payments = await make_provider(api).get_subscription_payment(PREAPPROVAL_ID)

    assert len(payments) == 1
    assert payments[0].payment_id == PREAPPROVAL_ID
    assert payments[0].status == "PENDING"
    assert payments[0].invoice_url == INIT_POINT
    assert payments[0].value == Decimal("129.9")
    assert payments[0].due_date == date(2026, 10, 1)
    assert payments[0].billing_type == "CREDIT_CARD"
    assert api.calls[1]["params"] == {"preapproval_id": PREAPPROVAL_ID}


async def test_subscription_with_invoices_returns_real_payments(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(status="authorized"),
            ("GET", "/authorized_payments/search"): {
                "results": [
                    {"id": 7001, "transaction_amount": 129.9, "debit_date": "2026-09-14T13:00:00.000-03:00", "payment": {"id": 123456, "status": "approved"}},
                    {"id": 7002, "transaction_amount": 129.9, "debit_date": "2027-03-14T13:00:00.000-03:00", "status": "scheduled"},
                ]
            },
        }
    )

    payments = await make_provider(api).get_subscription_payment(PREAPPROVAL_ID)

    assert [payment.payment_id for payment in payments] == ["123456"]
    assert payments[0].status == "RECEIVED"


async def test_cancel_subscription_puts_cancelled_status(fake_mp_api):
    api = fake_mp_api({("PUT", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(status="cancelled")})

    reference = await make_provider(api).cancel_subscription(PREAPPROVAL_ID)

    assert reference == PREAPPROVAL_ID
    assert api.calls[0]["payload"] == {"status": "cancelled"}


@pytest.mark.parametrize(
    ("status", "expected", "deleted"),
    [("authorized", "ACTIVE", False), ("pending", "PENDING", False), ("paused", "PAUSED", False), ("cancelled", "CANCELED", True)],
)
async def test_verify_status_translates_preapproval(fake_mp_api, status, expected, deleted):
    api = fake_mp_api({("GET", f"/preapproval/{PREAPPROVAL_ID}"): preapproval(status=status)})

    response = await make_provider(api).verify_status(PREAPPROVAL_ID)

    assert response.status == expected
    assert response.deleted is deleted
    assert response.cycle == "SEMIANNUALLY"
    assert response.value == Decimal("129.9")
    assert response.next_due_date == date(2026, 10, 1)


def test_gateway_factory_resolves_mercadopago():
    assert isinstance(GetGatewayInfra().get(GatewayProvider.MERCADOPAGO), MercadoPagoProvider)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mercadopago_provider_subscription.py -v`
Expected: FAIL com `NotImplementedError` e `UnsupportedGatewayError`.

- [ ] **Step 3: Implement**

Em `app/infra/interfaces/mercadopago_provider.py`:
- adicionar `time` ao import de `datetime`;
- acrescentar ao import de mappers: `CYCLE_BY_FREQUENCY_MONTHS`, `FREQUENCY_MONTHS_BY_CYCLE`, `preapproval_status_to_gateway`;
- substituir `create_subscription`, `get_subscription_payment`, `cancel_subscription` e `verify_status` por:

```python
    async def create_subscription(
        self,
        customer_provider_id: str,
        billing_type: PaymentType,
        value: Decimal,
        next_due_date: date,
        cycle: SubscriptionType,
        description: str,
        external_reference: str | None = None,
    ) -> str:
        if billing_type != PaymentType.CREDIT_CARD:
            raise DomainError("Mercado Pago so suporta assinaturas recorrentes com cartao de credito.")

        customer = await self.api.get(f"/v1/customers/{customer_provider_id}")
        payer_email = customer.get("email")
        if not payer_email:
            raise DomainError("Customer do Mercado Pago sem e-mail para a assinatura.")

        auto_recurring = {
            "frequency": FREQUENCY_MONTHS_BY_CYCLE[cycle],
            "frequency_type": "months",
            "transaction_amount": float(value),
            "currency_id": "BRL",
        }
        if next_due_date > self._now().date():
            auto_recurring["start_date"] = _iso(datetime.combine(next_due_date, time.min, tzinfo=timezone.utc))

        payload = {
            "reason": description,
            "payer_email": payer_email,
            "auto_recurring": auto_recurring,
            "back_url": settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL,
            # Sem cartao tokenizado no fluxo: o pagador conclui a autorizacao no init_point.
            "status": "pending",
        }
        if external_reference:
            payload["external_reference"] = external_reference

        response = await self.api.post(
            "/preapproval",
            payload,
            idempotency_key=f"preapproval:{external_reference}" if external_reference else None,
        )
        return str(response["id"])

    async def get_subscription_payment(self, subscription_id: str) -> list[SubscriptionPaymentResponse]:
        preapproval = await self.api.get(f"/preapproval/{subscription_id}")
        search = await self.api.get("/authorized_payments/search", params={"preapproval_id": subscription_id})

        payments = [
            SubscriptionPaymentResponse(
                payment_id=str(invoice["payment"]["id"]),
                status=payment_status_to_gateway(invoice["payment"].get("status")),
                due_date=(parse_datetime(invoice.get("debit_date")) or self._now()).date(),
                value=Decimal(str(invoice["transaction_amount"])),
                invoice_url=None,
                billing_type="CREDIT_CARD",
            )
            for invoice in search.get("results") or []
            if (invoice.get("payment") or {}).get("id")
        ]
        if payments:
            return payments

        # Antes da autorizacao nao existe fatura. O pagamento local nasce com o id da
        # assinatura e e revinculado quando a primeira fatura chega (process_webhook).
        next_payment = parse_datetime(preapproval.get("next_payment_date")) or self._now()
        return [
            SubscriptionPaymentResponse(
                payment_id=subscription_id,
                status="PENDING",
                due_date=next_payment.date(),
                value=Decimal(str(preapproval["auto_recurring"]["transaction_amount"])),
                invoice_url=preapproval.get("init_point"),
                billing_type="CREDIT_CARD",
            )
        ]

    async def cancel_subscription(self, subscription_id: str) -> str:
        response = await self.api.put(f"/preapproval/{subscription_id}", {"status": "cancelled"})
        return str(response.get("id") or subscription_id)

    async def verify_status(self, subscription_id: str) -> SubscriptionStatusResponse:
        preapproval = await self.api.get(f"/preapproval/{subscription_id}")
        recurring = preapproval.get("auto_recurring") or {}
        status = preapproval_status_to_gateway(preapproval.get("status"))
        next_payment = parse_datetime(preapproval.get("next_payment_date"))
        return SubscriptionStatusResponse(
            subscription_id=str(preapproval["id"]),
            status=status,
            deleted=status == "CANCELED",
            next_due_date=next_payment.date() if next_payment else self._now().date(),
            value=Decimal(str(recurring.get("transaction_amount", 0))),
            cycle=CYCLE_BY_FREQUENCY_MONTHS.get(int(recurring.get("frequency") or 1), "MONTHLY"),
        )
```

`app/infra/interfaces/gateway_provider.py`:

```python
from app.application.interfaces.gateway_provider import GetGateway, InterfaceGateway
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.errors import UnsupportedGatewayError
from app.infra.interfaces.asaas_provider import AsaasProvider
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider


class GetGatewayInfra(GetGateway):
    def __init__(self):
        self.providers: dict[GatewayProvider, type[InterfaceGateway]] = {
            GatewayProvider.ASAAS: AsaasProvider,
            GatewayProvider.MERCADOPAGO: MercadoPagoProvider,
        }

    def get(self, gateway: GatewayProvider) -> InterfaceGateway:
        provider_class = self.providers.get(gateway)
        if provider_class is None:
            raise UnsupportedGatewayError(f"Gateway não suportado: {gateway.value}")

        return provider_class()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mercadopago_provider_subscription.py -v` e depois `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/infra/interfaces/mercadopago_provider.py app/infra/interfaces/gateway_provider.py tests/test_mercadopago_provider_subscription.py
git commit -m "feat(mercadopago): preapproval subscriptions and gateway registration"
```

---

### Task 8: Resolver notificações no adapter

**Files:**
- Modify: `app/application/interfaces/gateway_provider.py` (`InterfaceGateway`)
- Modify: `app/infra/interfaces/mercadopago_provider.py` (`normalize_webhook` e `resolve_webhook`)
- Test: `tests/test_mercadopago_resolve_webhook.py`

**Interfaces:**
- Consumes: mappers `payment_notification_to_webhook`, `authorized_payment_to_webhook`, `preapproval_notification_to_webhook` (Task 5).
- Produces: `InterfaceGateway.resolve_webhook(payload: dict) -> WebhookPayload | None` (async, com padrão = `normalize_webhook`). No Mercado Pago, `resolve_webhook` usa `payload["type"]` (ou `topic`) e `payload["data"]["id"]`, e levanta `ValueError` quando falta o id.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mercadopago_resolve_webhook.py
import pytest

from app.application.dtos.request.webhook import EventType
from app.domain.errors import DomainError
from app.infra.interfaces.asaas_provider import AsaasProvider
from app.infra.interfaces.mercadopago_provider import MercadoPagoProvider

APPROVED_CHECKOUT_PAYMENT = {
    "id": 123456,
    "status": "approved",
    "date_approved": "2026-09-14T12:10:06.000-03:00",
    "payment_type_id": "bank_transfer",
    "transaction_amount": 72.0,
    "external_reference": "checkout:marketfy:order-123",
}


def notification(topic: str, resource_id: str = "123456") -> dict:
    return {"id": 99, "live_mode": False, "type": topic, "action": f"{topic}.updated", "data": {"id": resource_id}}


async def test_payment_topic_fetches_payment(fake_mp_api):
    api = fake_mp_api({("GET", "/v1/payments/123456"): APPROVED_CHECKOUT_PAYMENT})

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("payment"))

    assert payload.event == EventType.CHECKOUT_PAID
    assert api.calls[0]["endpoint"] == "/v1/payments/123456"


async def test_authorized_payment_topic_fetches_invoice_and_payment(fake_mp_api):
    api = fake_mp_api(
        {
            ("GET", "/authorized_payments/7001"): {"id": 7001, "preapproval_id": "2c93", "payment": {"id": 123456, "status": "approved"}},
            ("GET", "/v1/payments/123456"): APPROVED_CHECKOUT_PAYMENT | {"external_reference": "sub_1", "payment_type_id": "credit_card"},
        }
    )

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("subscription_authorized_payment", "7001"))

    assert payload.event == EventType.PAYMENT_RECEIVED
    assert payload.details.subscription == "2c93"


async def test_scheduled_invoice_without_payment_is_ignored_without_payment_lookup(fake_mp_api):
    api = fake_mp_api({("GET", "/authorized_payments/7002"): {"id": 7002, "preapproval_id": "2c93", "status": "scheduled"}})

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("subscription_authorized_payment", "7002"))

    assert payload is None
    assert len(api.calls) == 1


async def test_preapproval_topic_fetches_preapproval(fake_mp_api):
    api = fake_mp_api({("GET", "/preapproval/2c93"): {"id": "2c93", "status": "cancelled"}})

    payload = await MercadoPagoProvider(api=api).resolve_webhook(notification("subscription_preapproval", "2c93"))

    assert payload.event == EventType.SUBSCRIPTION_INACTIVATED


async def test_unknown_topic_is_ignored(fake_mp_api):
    api = fake_mp_api({})

    assert await MercadoPagoProvider(api=api).resolve_webhook(notification("topic_claims_integration_wh")) is None
    assert api.calls == []


async def test_notification_without_data_id_is_rejected(fake_mp_api):
    with pytest.raises(ValueError):
        await MercadoPagoProvider(api=fake_mp_api({})).resolve_webhook({"type": "payment", "data": {}})


def test_mercadopago_normalize_webhook_requires_resolution(fake_mp_api):
    with pytest.raises(DomainError):
        MercadoPagoProvider(api=fake_mp_api({})).normalize_webhook(notification("payment"))


async def test_asaas_resolve_webhook_defaults_to_normalize():
    payload = await AsaasProvider().resolve_webhook(
        {"id": "evt-1", "event": "CHECKOUT_PAID", "checkout": {"id": "checkout_123", "status": "PAID", "externalReference": "checkout:marketfy:order-123", "items": []}}
    )

    assert payload.event == EventType.CHECKOUT_PAID
    assert payload.details.id == "checkout_123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mercadopago_resolve_webhook.py -v`
Expected: FAIL com `AttributeError: ... has no attribute 'resolve_webhook'`.

- [ ] **Step 3: Implement**

Em `InterfaceGateway` (`app/application/interfaces/gateway_provider.py`), logo depois de `normalize_webhook`:

```python
    async def resolve_webhook(self, payload: dict) -> WebhookPayload | None:
        """Converte a notificacao bruta no payload normalizado; None quando nao ha acao.

        Gateways que enviam o estado completo so normalizam. Gateways que enviam apenas
        o id do recurso sobrescrevem este metodo para buscar o estado na API.
        """
        return self.normalize_webhook(payload)
```

Em `app/infra/interfaces/mercadopago_provider.py`:
- acrescentar ao import de mappers: `authorized_payment_to_webhook`, `payment_notification_to_webhook`, `preapproval_notification_to_webhook`;
- substituir `normalize_webhook` por:

```python
    def normalize_webhook(self, payload: dict) -> WebhookPayload:
        raise DomainError("Notificacoes do Mercado Pago trazem so o id do recurso; use resolve_webhook.")

    async def resolve_webhook(self, payload: dict) -> WebhookPayload | None:
        topic = payload.get("type") or payload.get("topic")
        resource_id = str((payload.get("data") or {}).get("id") or "")
        if not resource_id:
            raise ValueError("Notificacao do Mercado Pago sem data.id.")

        if topic == "payment":
            return payment_notification_to_webhook(await self.api.get(f"/v1/payments/{resource_id}"))

        if topic == "subscription_authorized_payment":
            invoice = await self.api.get(f"/authorized_payments/{resource_id}")
            payment_id = (invoice.get("payment") or {}).get("id")
            payment = await self.api.get(f"/v1/payments/{payment_id}") if payment_id else None
            return authorized_payment_to_webhook(invoice, payment)

        if topic == "subscription_preapproval":
            return preapproval_notification_to_webhook(await self.api.get(f"/preapproval/{resource_id}"))

        return None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_mercadopago_resolve_webhook.py tests/test_asaas_webhook_normalization.py -v` e depois `python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/application/interfaces/gateway_provider.py app/infra/interfaces/mercadopago_provider.py tests/test_mercadopago_resolve_webhook.py
git commit -m "feat(mercadopago): resolve thin notifications into normalized webhook payloads"
```

---

### Task 9: Endpoint de webhook do Mercado Pago e job de resolução

**Files:**
- Create: `app/infra/interfaces/mercadopago_signature.py`
- Modify: `app/web/dependencies/security.py`
- Modify: `app/web/routes/webhooks.py`
- Modify: `app/workers/tasks.py` (novo `process_gateway_notification`)
- Modify: `app/workers/worker.py` (import e `func(...)`)
- Test: `tests/test_mercadopago_signature.py`, `tests/test_mercadopago_webhook_api.py`, `tests/test_gateway_notification_worker.py`

**Interfaces:**
- Consumes: `InterfaceGateway.resolve_webhook` (Task 8), `_handle_gateway_api_error` (Task 1), `process_webhook` (existente).
- Produces:
  - `is_valid_signature(*, secret: str, signature_header: str | None, request_id: str | None, data_id: str | None) -> bool`.
  - `validate_mercadopago_webhook` (dependency que devolve `WebhookValidationResult`).
  - A rota `POST /v1/webhooks/mercadopago` enfileira `workers:tasks.process_gateway_notification(raw_payload: dict, gateway_provider_name: str)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mercadopago_signature.py
import hashlib
import hmac

from app.infra.interfaces.mercadopago_signature import is_valid_signature

SECRET = "mercadopago-secret"


def sign(manifest: str) -> str:
    return hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()


def test_valid_signature_matches_documented_manifest():
    v1 = sign("id:123456;request-id:req-1;ts:1704908010;")

    assert is_valid_signature(secret=SECRET, signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="123456")


def test_alphanumeric_data_id_is_lowercased_in_manifest():
    v1 = sign("id:ord01jq4s4ky8hwq6na5pxb65b3d3;request-id:req-1;ts:1704908010;")

    assert is_valid_signature(
        secret=SECRET, signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="ORD01JQ4S4KY8HWQ6NA5PXB65B3D3"
    )


def test_missing_request_id_is_removed_from_manifest():
    v1 = sign("id:123456;ts:1704908010;")

    assert is_valid_signature(secret=SECRET, signature_header=f"ts=1704908010, v1={v1}", request_id=None, data_id="123456")


def test_tampered_or_missing_signature_is_rejected():
    v1 = sign("id:123456;request-id:req-1;ts:1704908010;")

    assert not is_valid_signature(secret=SECRET, signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="999")
    assert not is_valid_signature(secret=SECRET, signature_header=None, request_id="req-1", data_id="123456")
    assert not is_valid_signature(secret=SECRET, signature_header="v1=abc", request_id="req-1", data_id="123456")
    assert not is_valid_signature(secret="", signature_header=f"ts=1704908010,v1={v1}", request_id="req-1", data_id="123456")
```

```python
# tests/test_mercadopago_webhook_api.py
import hashlib
import hmac
import json

from app.infra.config import settings

SECRET = "fake-mercadopago-webhook-secret-with-32-chars"


def signed_headers(data_id: str, request_id: str = "req-1", ts: str = "1704908010") -> dict:
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return {"x-signature": f"ts={ts},v1={v1}", "x-request-id": request_id, "content-type": "application/json"}


def notification(data_id: str = "123456") -> dict:
    return {"id": 12345, "live_mode": False, "type": "payment", "action": "payment.updated", "data": {"id": data_id}}


def test_mercadopago_webhook_enqueues_notification_resolution(client, fake_redis, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)

    response = client.post(
        "/v1/webhooks/mercadopago?data.id=123456&type=payment",
        content=json.dumps(notification()),
        headers=signed_headers("123456"),
    )

    assert response.status_code == 200
    jobs = [args for args, _ in fake_redis.enqueued_jobs if args[0] == "workers:tasks.process_gateway_notification"]
    assert jobs == [("workers:tasks.process_gateway_notification", notification(), "MERCADOPAGO")]


def test_mercadopago_webhook_rejects_invalid_signature(client, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)
    headers = signed_headers("123456") | {"x-signature": "ts=1704908010,v1=deadbeef"}

    response = client.post("/v1/webhooks/mercadopago?data.id=123456&type=payment", content=json.dumps(notification()), headers=headers)

    assert response.status_code == 401


def test_mercadopago_webhook_marks_replayed_body_as_duplicate(client, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)
    body = json.dumps(notification())

    first = client.post("/v1/webhooks/mercadopago?data.id=123456&type=payment", content=body, headers=signed_headers("123456"))
    second = client.post("/v1/webhooks/mercadopago?data.id=123456&type=payment", content=body, headers=signed_headers("123456"))

    assert first.status_code == 200
    assert second.json() == {"received": True, "duplicate": True}


def test_mercadopago_webhook_requires_data_id_in_body(client, monkeypatch):
    monkeypatch.setattr(settings, "MERCADOPAGO_WEBHOOK_SECRET", SECRET)
    manifest_without_id = "request-id:req-1;ts:1704908010;"
    v1 = hmac.new(SECRET.encode(), manifest_without_id.encode(), hashlib.sha256).hexdigest()
    headers = {"x-signature": f"ts=1704908010,v1={v1}", "x-request-id": "req-1", "content-type": "application/json"}

    response = client.post("/v1/webhooks/mercadopago", content=json.dumps({"type": "payment", "data": {}}), headers=headers)

    assert response.status_code == 400
```

```python
# tests/test_gateway_notification_worker.py
from types import SimpleNamespace

import pytest

from app.application.dtos.request.webhook import WebhookPayload
from app.workers import tasks

NOTIFICATION = {"id": 1, "type": "payment", "data": {"id": "123456"}}


def make_ctx(fake_redis):
    return {
        "job_id": "job-mp-1",
        "job_try": 1,
        "redis": fake_redis,
        "logger": SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
    }


class FakeGateway:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc

    async def resolve_webhook(self, payload):
        if self.exc:
            raise self.exc
        return self.result


def patch_gateway(monkeypatch, gateway):
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: SimpleNamespace(get=lambda provider: gateway))


async def test_ignored_notification_completes_without_processing(monkeypatch, fake_redis):
    patch_gateway(monkeypatch, FakeGateway(result=None))

    async def fail_process_webhook(*args, **kwargs):
        raise AssertionError("nao deveria processar")

    monkeypatch.setattr(tasks, "process_webhook", fail_process_webhook)

    response = await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")

    assert response == {"status": "ignored", "result": None}


async def test_resolved_notification_is_delegated_to_process_webhook(monkeypatch, fake_redis):
    payload = WebhookPayload.model_validate(
        {"event": "CHECKOUT_PAID", "source_event_id": "payment:123456:approved", "details": {"id": "123456", "external_reference": "checkout:marketfy:1"}}
    )
    patch_gateway(monkeypatch, FakeGateway(result=payload))
    received = {}

    async def fake_process_webhook(ctx, payload_dict, gateway_provider_str):
        received["payload"] = payload_dict
        received["provider"] = gateway_provider_str
        return {"status": "success"}

    monkeypatch.setattr(tasks, "process_webhook", fake_process_webhook)

    response = await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")

    assert response == {"status": "success"}
    assert received["provider"] == "MERCADOPAGO"
    assert received["payload"]["event"] == "CHECKOUT_PAID"


async def test_invalid_notification_is_terminal(monkeypatch, fake_redis):
    patch_gateway(monkeypatch, FakeGateway(exc=ValueError("Notificacao do Mercado Pago sem data.id.")))

    response = await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")

    assert response["status"] == "failed"


async def test_unexpected_resolution_error_is_retried(monkeypatch, fake_redis):
    patch_gateway(monkeypatch, FakeGateway(exc=RuntimeError("network down")))

    with pytest.raises(RuntimeError):
        await tasks.process_gateway_notification(make_ctx(fake_redis), NOTIFICATION, "MERCADOPAGO")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mercadopago_signature.py tests/test_mercadopago_webhook_api.py tests/test_gateway_notification_worker.py -v`
Expected: FAIL (módulo inexistente, rota 404/405 e `AttributeError: process_gateway_notification`).

- [ ] **Step 3: Implement the signature**

```python
# app/infra/interfaces/mercadopago_signature.py
"""Validacao do header x-signature dos webhooks do Mercado Pago.

Manifest documentado: id:[data.id_url];request-id:[x-request-id_header];ts:[ts_header];
data.id vai em minusculas e valores ausentes saem do manifest.
"""
import hashlib
import hmac


def _parse_signature_header(header: str) -> tuple[str | None, str | None]:
    values: dict[str, str] = {}
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        values[key.strip()] = value.strip()
    return values.get("ts"), values.get("v1")


def _build_manifest(*, data_id: str | None, request_id: str | None, ts: str) -> str:
    manifest = ""
    if data_id:
        manifest += f"id:{data_id.lower()};"
    if request_id:
        manifest += f"request-id:{request_id};"
    return manifest + f"ts:{ts};"


def is_valid_signature(*, secret: str, signature_header: str | None, request_id: str | None, data_id: str | None) -> bool:
    if not secret or not signature_header:
        return False

    ts, received = _parse_signature_header(signature_header)
    if not ts or not received:
        return False

    manifest = _build_manifest(data_id=data_id, request_id=request_id, ts=ts)
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
```

- [ ] **Step 4: Implement the dependency**

Em `app/web/dependencies/security.py`, adicionar `from app.infra.interfaces.mercadopago_signature import is_valid_signature` e trocar `validate_asaas_webhook` por este bloco. As validações do Asaas continuam iguais e na mesma ordem; só foram extraídas para reuso.

```python
def _ensure_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    if content_type and "application/json" not in content_type.lower():
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Webhook deve usar content-type application/json.",
        )


async def _read_webhook_body(request: Request, redis) -> WebhookValidationResult:
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook e obrigatorio.",
        )

    if len(raw_body) > settings.MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload do webhook excede o tamanho maximo permitido.",
        )

    replay_hash = hashlib.sha256(raw_body).hexdigest()
    replay_key = f"billing_core:webhook_replay:{replay_hash}"
    if await redis.get(replay_key) is not None:
        return WebhookValidationResult(raw_body=raw_body, replay_key=replay_key, duplicate=True)

    return WebhookValidationResult(raw_body=raw_body, replay_key=replay_key)


async def validate_asaas_webhook(
    request: Request,
    redis=Depends(get_redis_pool),
    asaas_access_token: str | None = Header(
        default=None,
        alias="asaas-access-token",
        description=(
            "Secret compartilhado com o Asaas para autenticar o webhook. "
            "Deve corresponder a `ASAAS_WEBHOOK_SECRET`."
        ),
        examples=["whsec_xpto123"],
    ),
):
    _ensure_json_content_type(request)

    if not asaas_access_token or not hmac.compare_digest(asaas_access_token, settings.ASAAS_WEBHOOK_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook nao autorizado.",
        )

    return await _read_webhook_body(request, redis)


async def validate_mercadopago_webhook(
    request: Request,
    redis=Depends(get_redis_pool),
    x_signature: str | None = Header(
        default=None,
        alias="x-signature",
        description="Assinatura HMAC do Mercado Pago no formato `ts=<timestamp>,v1=<hash>`.",
    ),
    x_request_id: str | None = Header(
        default=None,
        alias="x-request-id",
        description="Identificador da notificacao usado no manifest da assinatura.",
    ),
):
    _ensure_json_content_type(request)

    if not is_valid_signature(
        secret=settings.MERCADOPAGO_WEBHOOK_SECRET or "",
        signature_header=x_signature,
        request_id=x_request_id,
        data_id=request.query_params.get("data.id"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook nao autorizado.",
        )

    return await _read_webhook_body(request, redis)
```

- [ ] **Step 5: Implement the route**

Em `app/web/routes/webhooks.py`, trocar o import de segurança por `from app.web.dependencies.security import WebhookValidationResult, validate_asaas_webhook, validate_mercadopago_webhook` e adicionar ao fim do arquivo:

```python
@router.post(
    "/mercadopago",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(webhook_rate_limit())],
    summary="Receber webhook Mercado Pago",
    description="""
Recebe uma notificacao do Mercado Pago, valida a assinatura `x-signature`, protege contra replay e enfileira a resolucao.

### Fluxo
1. O Mercado Pago envia `type` e `data.id` (query string e corpo).
2. O Billing Core valida o HMAC SHA256 do manifest `id:<data.id>;request-id:<x-request-id>;ts:<ts>;` com `MERCADOPAGO_WEBHOOK_SECRET`.
3. O worker busca o recurso na API do Mercado Pago e segue o mesmo processamento do webhook do Asaas.

### Topicos tratados
- `payment` (checkout avulso e estornos)
- `subscription_authorized_payment` (faturas de assinatura)
- `subscription_preapproval` (cancelamento de assinatura)
""",
    responses=build_error_responses(400, 401, 413, 415, 429, 500),
)
async def receive_mercadopago_webhook(
    http_request: Request,
    webhook_validation: WebhookValidationResult = Depends(validate_mercadopago_webhook),
    redis=Depends(get_redis_pool),
):
    if webhook_validation.duplicate:
        return {"received": True, "duplicate": True}

    try:
        payload = json.loads(webhook_validation.raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload do webhook deve ser um JSON valido.",
        ) from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notificacao do Mercado Pago sem data.id.",
        )

    gateway_provider = GatewayProvider.MERCADOPAGO
    job = await redis.enqueue_job(
        "workers:tasks.process_gateway_notification",
        payload,
        gateway_provider.name,
    )
    await update_job_metadata(
        redis,
        job.job_id,
        status="queued",
        job_name="process_gateway_notification",
        attempt=0,
        max_tries=settings.WORKER_MAX_TRIES,
        request_id=http_request.state.request_id,
        created_at=datetime.now(timezone.utc),
        provider=gateway_provider.value,
        resource_type="webhook",
        source_event_id=str(payload.get("id") or data["id"]),
    )
    await redis.setex(
        webhook_validation.replay_key,
        settings.WEBHOOK_REPLAY_TTL_SECONDS,
        "1",
    )

    return {"job_id": job.job_id, "message": "Webhook recebido para processamento."}
```

- [ ] **Step 6: Implement the worker job**

Em `app/workers/tasks.py`, logo depois de `process_webhook`:

```python
async def process_gateway_notification(ctx, raw_payload: dict, gateway_provider_str: str):
    """Notificacao que so traz o id do recurso: busca o estado no gateway e segue o fluxo normal."""
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]
    gateway_provider = GatewayProvider[gateway_provider_str.upper()]

    try:
        gateway = GetGatewayInfra().get(gateway_provider)
        payload = await gateway.resolve_webhook(raw_payload)
    except (DomainError, NotFoundError, ValueError) as exc:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "process_gateway_notification", job_id)
        ctx["logger"].warning("Gateway notification rejected", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except GatewayAPIError as exc:
        return await _handle_gateway_api_error(
            ctx, exc, job_name="process_gateway_notification", job_id=job_id, job_try=job_try, label="Gateway notification resolution"
        )
    except Exception as exc:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "process_gateway_notification", job_id)
        ctx["logger"].error("Gateway notification resolution failed", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        raise

    if payload is None:
        await update_job_metadata(ctx["redis"], job_id, status="completed", finished_at=datetime.now(timezone.utc))
        ctx["logger"].info(
            "Gateway notification ignored",
            extra={"job_id": job_id, "provider": gateway_provider.value, "topic": raw_payload.get("type")},
        )
        return {"status": "ignored", "result": None}

    return await process_webhook(ctx, payload.model_dump(mode="json"), gateway_provider.name)
```

Em `app/workers/worker.py`, adicionar `process_gateway_notification` ao import de `.tasks` e, na lista `functions=[...]`, logo depois do `func(process_webhook, ...)` existente:

```python
            func(
                process_gateway_notification,
                name="workers:tasks.process_gateway_notification",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
```

- [ ] **Step 7: Run tests**

Run: `python -m pytest tests/test_mercadopago_signature.py tests/test_mercadopago_webhook_api.py tests/test_gateway_notification_worker.py tests/test_api_contracts.py -v` e depois `python -m pytest -q`
Expected: PASS, incluindo os testes existentes de `/v1/webhooks/asaas`.

- [ ] **Step 8: Commit**

```bash
git add app/infra/interfaces/mercadopago_signature.py app/web/dependencies/security.py app/web/routes/webhooks.py app/workers/tasks.py app/workers/worker.py tests/test_mercadopago_signature.py tests/test_mercadopago_webhook_api.py tests/test_gateway_notification_worker.py
git commit -m "feat(webhooks): receive signed Mercado Pago notifications and resolve them in the worker"
```

---

### Task 10: Revincular o pagamento inicial da assinatura

**Files:**
- Modify: `app/application/use_cases/process_webhook.py` (ramos `PAYMENT_RECEIVED` e `PAYMENT_CONFIRMED` com assinatura)
- Test: `tests/test_process_webhook_use_case.py`

**Interfaces:**
- Consumes: o contrato da Task 7, em que o pagamento provisório tem `provider_payment_id == subscription.gateway_subscription_id`.
- Produces: `ProcessWebhookService._find_subscription_payment(sub, provider_payment_id: str) -> Payment | None`.

- [ ] **Step 1: Write the failing test**

Adicionar ao fim de `tests/test_process_webhook_use_case.py`:

```python
@pytest.mark.asyncio
async def test_first_invoice_takes_over_provisional_subscription_payment():
    subscription = make_subscription()
    subscription.gateway_provider = GatewayProvider.MERCADOPAGO
    provisional = Payment.create_subscription_payment(
        description="Pagamento relacionado a assinatura: Plano Pro",
        gateway=GatewayProvider.MERCADOPAGO,
        system_payment_id="sub-1:gw-sub-1",
        provider_payment_id="gw-sub-1",
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=subscription.id,
        checkout_link="https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id=gw-sub-1",
        payment_type=PaymentType.CREDIT_CARD,
    )
    provisional.id = uuid4()
    payment_repo = FakePaymentRepo(existing=provisional)
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=FakeSubscriptionRepo(subscription),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="authorized_payment:7001:approved",
        details=make_details(subscription="gw-sub-1", payment_id="mp-pay-1"),
    )

    result = await service.execute(GatewayProvider.MERCADOPAGO, payload)

    assert result.payment_id == provisional.id
    assert provisional.provider_payment_id == "mp-pay-1"
    assert provisional.payment_status == PaymentStatus.PAID
    assert {id(saved) for saved in payment_repo.saved} == {id(provisional)}
    assert subscription.status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_provisional_payment_of_another_subscription_is_not_taken_over():
    subscription = make_subscription()
    foreign = Payment.create_subscription_payment(
        description="Outra assinatura",
        gateway=GatewayProvider.MERCADOPAGO,
        system_payment_id="sub-x:gw-sub-1",
        provider_payment_id="gw-sub-1",
        value=Decimal("99.90"),
        from_system=System.NEECTIFY_SHOP,
        subscription_id=uuid4(),
        payment_type=PaymentType.CREDIT_CARD,
    )
    foreign.id = uuid4()
    payment_repo = FakePaymentRepo(existing=foreign)
    service = ProcessWebhookService(
        payment_repo=payment_repo,
        sub_repo=FakeSubscriptionRepo(subscription),
        uow=FakeUow(),
        webhook_event_repo=FakeWebhookEventRepo(),
    )
    payload = WebhookPayload(
        event=EventType.PAYMENT_RECEIVED,
        source_event_id="authorized_payment:7001:approved",
        details=make_details(subscription="gw-sub-1", payment_id="mp-pay-1"),
    )

    result = await service.execute(GatewayProvider.MERCADOPAGO, payload)

    assert foreign.provider_payment_id == "gw-sub-1"
    assert result.payment_id != foreign.id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_process_webhook_use_case.py::test_first_invoice_takes_over_provisional_subscription_payment -v`
Expected: FAIL: `result.payment_id` é de um pagamento novo e `provisional.provider_payment_id` continua `"gw-sub-1"`.

- [ ] **Step 3: Implement**

Em `ProcessWebhookService`, adicionar o método:

```python
    async def _find_subscription_payment(self, sub, provider_payment_id: str) -> Payment | None:
        payment = await self.payment_repo.get_by_provider_id(provider_payment_id)
        if payment is not None:
            return payment

        # Gateways sem cobranca imediata (Mercado Pago) criam o pagamento inicial com o id
        # da propria assinatura; a primeira fatura real assume esse registro.
        provisional = await self.payment_repo.get_by_provider_id(sub.gateway_subscription_id)
        if (
            provisional is not None
            and provisional.subscription_id == sub.id
            and provisional.payment_status == PaymentStatus.PENDING
        ):
            provisional.provider_payment_id = provider_payment_id
            return provisional

        return None
```

Nos ramos `if payload.event == EventType.PAYMENT_RECEIVED and payload.details.subscription:` e `if payload.event == EventType.PAYMENT_CONFIRMED and payload.details.subscription:`, trocar a linha

```python
            payment = await self.payment_repo.get_by_provider_id(payload.details.id)
```

por

```python
            payment = await self._find_subscription_payment(sub, payload.details.id)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_process_webhook_use_case.py -v` e depois `python -m pytest -q`
Expected: PASS, incluindo todos os testes do Asaas já existentes.

- [ ] **Step 5: Commit**

```bash
git add app/application/use_cases/process_webhook.py tests/test_process_webhook_use_case.py
git commit -m "feat(webhooks): first subscription invoice takes over the provisional payment"
```

---

### Task 11: Sincronizar checkouts pendentes (expiração no Mercado Pago)

**Files:**
- Modify: `app/application/repositories/payment_repo.py`
- Modify: `app/infra/repo/payment_repo.py`
- Create: `app/application/use_cases/sync_checkout_status.py`
- Modify: `app/workers/tasks.py` (novo `sync_pending_checkouts_worker`)
- Modify: `app/workers/worker.py` (`func` e `cron`)
- Test: `tests/test_sync_checkout_status_use_case.py`, `tests/test_sync_pending_checkouts_worker.py`

**Interfaces:**
- Consumes: `get_checkout` do Mercado Pago com status `ACTIVE`/`PAID`/`EXPIRED` (Task 6); `_build_payment_internal_delivery` (existente).
- Produces:
  - `PaymentRepository.list_pending_checkouts(gateway: GatewayProvider, limit: int) -> list[Payment]`.
  - `SyncCheckoutStatus(get_gateway, uow, payment_repo).execute(payment: Payment) -> Payment | None`.
  - `tasks.sync_pending_checkouts_worker(ctx) -> dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sync_checkout_status_use_case.py
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.interfaces.gateway_provider import CreateCheckoutGatewayResponse
from app.application.use_cases.sync_checkout_status import SyncCheckoutStatus
from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.system import System
from app.domain.errors import DomainError

REFERENCE = "checkout:marketfy:order-123"


class FakeGateway:
    def __init__(self, status: str, external_reference: str = REFERENCE):
        self.status = status
        self.external_reference = external_reference

    async def get_checkout(self, checkout_id):
        return CreateCheckoutGatewayResponse(
            checkout_id=checkout_id, checkout_url="https://mp/pref-1", status=self.status, external_reference=self.external_reference
        )


class FakeGetGateway:
    def __init__(self, gateway):
        self.gateway = gateway

    def get(self, gateway):
        return self.gateway


class FakePaymentRepo:
    def __init__(self):
        self.saved = []

    async def save(self, payment):
        self.saved.append(payment)
        return payment


class FakeUow:
    def __init__(self):
        self.commit_called = 0

    async def commit(self):
        self.commit_called += 1


def make_payment() -> Payment:
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.MERCADOPAGO,
        system_payment_id="order-123",
        provider_payment_id="pref-1",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://mp/pref-1",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference=REFERENCE,
    )
    payment.id = uuid4()
    return payment


def make_service(gateway, repo=None, uow=None):
    return SyncCheckoutStatus(get_gateway=FakeGetGateway(gateway), uow=uow or FakeUow(), payment_repo=repo or FakePaymentRepo())


@pytest.mark.parametrize(
    ("remote_status", "local_status"),
    [("PAID", PaymentStatus.PAID), ("EXPIRED", PaymentStatus.EXPIRED)],
)
async def test_final_remote_status_is_applied_and_committed(remote_status, local_status):
    repo, uow = FakePaymentRepo(), FakeUow()
    payment = make_payment()

    result = await make_service(FakeGateway(remote_status), repo, uow).execute(payment)

    assert result.payment_status == local_status
    assert repo.saved == [payment]
    assert uow.commit_called == 1


async def test_active_checkout_is_left_untouched():
    repo = FakePaymentRepo()

    assert await make_service(FakeGateway("ACTIVE"), repo).execute(make_payment()) is None
    assert repo.saved == []


async def test_divergent_reference_is_rejected():
    with pytest.raises(DomainError):
        await make_service(FakeGateway("EXPIRED", external_reference="checkout:marketfy:other")).execute(make_payment())
```

```python
# tests/test_sync_pending_checkouts_worker.py
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.domain.entities.payment import Payment
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.system import System
from app.workers import tasks


class DummySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def make_payment() -> Payment:
    payment = Payment.create_standalone_payment(
        description="Pedido 123",
        gateway=GatewayProvider.MERCADOPAGO,
        system_payment_id="order-123",
        provider_payment_id="pref-1",
        value=Decimal("72.00"),
        from_system=System.MARKETFY,
        checkout_link="https://mp/pref-1",
        webhook_link="https://hooks.neectify.local/billing/payment",
        due_date=None,
        external_reference="checkout:marketfy:order-123",
    )
    payment.id = uuid4()
    return payment


async def test_sync_worker_expires_checkout_and_enqueues_internal_delivery(monkeypatch, fake_redis):
    pending = make_payment()
    listed_gateways = []
    saved_deliveries = []

    class FakePaymentRepo:
        def __init__(self, session):
            pass

        async def list_pending_checkouts(self, gateway, limit):
            listed_gateways.append(gateway)
            return [pending]

    class FakeDeliveryRepo:
        def __init__(self, session):
            pass

        async def get_by_dedupe_key(self, dedupe_key):
            return None

        async def save(self, delivery):
            delivery.id = uuid4()
            saved_deliveries.append(delivery)
            return delivery

    class FakeSync:
        async def execute(self, payment):
            payment.mark_as_expired()
            return payment

    async def _noop():
        return None

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: DummySession())
    monkeypatch.setattr(tasks, "PaymentRepositoryINFRA", FakePaymentRepo)
    monkeypatch.setattr(tasks, "InternalWebhookDeliveryRepositoryINFRA", FakeDeliveryRepo)
    monkeypatch.setattr(tasks, "UowProvider", lambda session: SimpleNamespace(commit=_noop, rollback=_noop))
    monkeypatch.setattr(tasks, "GetGatewayInfra", lambda: object())
    monkeypatch.setattr(tasks, "SyncCheckoutStatus", lambda **kwargs: FakeSync())

    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    response = await tasks.sync_pending_checkouts_worker(ctx)

    assert response == {"status": "success", "updated": 1}
    assert listed_gateways == [GatewayProvider.MERCADOPAGO]
    assert saved_deliveries[0].payload["payment_status"] == "expired"
    delivery_jobs = [args for args, _ in fake_redis.enqueued_jobs if args[0] == "workers:tasks.send_internal_webhook"]
    assert len(delivery_jobs) == 1
    assert "billing_core:sync_checkouts_lock" not in fake_redis.values


async def test_sync_worker_skips_when_lock_is_held(fake_redis):
    fake_redis.values["billing_core:sync_checkouts_lock"] = "locked"
    ctx = {"redis": fake_redis, "logger": SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)}

    assert await tasks.sync_pending_checkouts_worker(ctx) == {"status": "skipped", "reason": "lock_held"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sync_checkout_status_use_case.py tests/test_sync_pending_checkouts_worker.py -v`
Expected: FAIL com `ModuleNotFoundError: app.application.use_cases.sync_checkout_status` e `AttributeError: sync_pending_checkouts_worker`.

- [ ] **Step 3: Implement the repository method**

`app/application/repositories/payment_repo.py`: adicionar `from app.domain.enums.gateway_provider import GatewayProvider` e, antes de `save`:

```python
    @abstractmethod
    async def list_pending_checkouts(self, gateway: GatewayProvider, limit: int) -> list[Payment]:
        """Checkouts avulsos ainda pendentes no gateway, do atualizado ha mais tempo ao mais recente."""
        pass
```

`app/infra/repo/payment_repo.py`: adicionar os imports `GatewayProvider`, `MovimentationType`, `PaymentStatus` e, antes de `save`:

```python
    async def list_pending_checkouts(self, gateway: GatewayProvider, limit: int) -> list[Payment]:
        stmt = (
            select(PaymentModel)
            .where(
                PaymentModel.gateway == gateway,
                PaymentModel.payment_status == PaymentStatus.PENDING,
                PaymentModel.movimentation_type == MovimentationType.DEFAULT_PAYMENT,
                PaymentModel.subscription_id.is_(None),
                PaymentModel.checkout_link.is_not(None),
            )
            .order_by(PaymentModel.updated_at.asc())
            .limit(limit)
        )
        r = await self.session.execute(stmt)
        return [x.to_domain() for x in r.scalars().all()]
```

- [ ] **Step 4: Implement the use case**

```python
# app/application/use_cases/sync_checkout_status.py
from datetime import datetime, timezone

from app.application.interfaces.gateway_provider import GetGateway
from app.application.interfaces.uow_provider import UowProvider
from app.application.repositories.payment_repo import PaymentRepository
from app.domain.entities.payment import Payment
from app.domain.errors import DomainError


class SyncCheckoutStatus:
    """Aplica o status remoto de um checkout pendente.

    Existe para gateways que nao notificam a expiracao do checkout (Mercado Pago).
    """

    def __init__(self, get_gateway: GetGateway, uow: UowProvider, payment_repo: PaymentRepository):
        self.get_gateway = get_gateway
        self.uow = uow
        self.payment_repo = payment_repo

    async def execute(self, payment: Payment) -> Payment | None:
        gateway = self.get_gateway.get(payment.gateway)
        checkout = await gateway.get_checkout(payment.provider_payment_id)
        if checkout.external_reference != payment.external_reference:
            raise DomainError("Checkout remoto retornou external_reference divergente durante sincronizacao.")

        remote_status = checkout.status.upper()
        if remote_status == "PAID":
            payment.mark_as_paid(payment_date=datetime.now(timezone.utc))
        elif remote_status == "EXPIRED":
            payment.mark_as_expired()
        elif remote_status == "CANCELED":
            payment.mark_as_canceled()
        else:
            return None

        payment = await self.payment_repo.save(payment)
        await self.uow.commit()
        return payment
```

- [ ] **Step 5: Implement the cron worker**

Em `app/workers/tasks.py`, adicionar `from app.application.use_cases.sync_checkout_status import SyncCheckoutStatus` e, ao fim do arquivo:

```python
SYNC_CHECKOUTS_BATCH_SIZE = 100


async def sync_pending_checkouts_worker(ctx):
    """Cron: aplica PAID/EXPIRED a checkouts de gateways que nao notificam expiracao."""
    lock_key = "billing_core:sync_checkouts_lock"
    lock_acquired = await ctx["redis"].set(lock_key, "locked", ex=240, nx=True)
    if not lock_acquired:
        return {"status": "skipped", "reason": "lock_held"}

    updated = 0
    try:
        async with AsyncSessionLocal() as session:
            pending_payments = await PaymentRepositoryINFRA(session).list_pending_checkouts(
                GatewayProvider.MERCADOPAGO, SYNC_CHECKOUTS_BATCH_SIZE
            )

        for pending in pending_payments:
            internal_delivery_id: UUID | None = None
            async with AsyncSessionLocal() as session:
                payment_repo = PaymentRepositoryINFRA(session)
                delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
                uow = UowProvider(session)
                service = SyncCheckoutStatus(get_gateway=GetGatewayInfra(), uow=uow, payment_repo=payment_repo)
                try:
                    payment = await service.execute(pending)
                    if payment is None:
                        continue
                    updated += 1
                    delivery = await _build_payment_internal_delivery(payment)
                    if delivery is not None and await delivery_repo.get_by_dedupe_key(delivery.dedupe_key) is None:
                        delivery = await delivery_repo.save(delivery)
                        await uow.commit()
                        internal_delivery_id = delivery.id
                except Exception as exc:
                    await uow.rollback()
                    ctx["logger"].error(
                        "Failed to sync pending checkout",
                        extra={"payment_id": str(pending.id), "error": str(exc)},
                    )
                    continue

            if internal_delivery_id is not None:
                await ctx["redis"].enqueue_job("workers:tasks.send_internal_webhook", str(internal_delivery_id))
    finally:
        await ctx["redis"].delete(lock_key)

    ctx["logger"].info("Pending checkouts synced", extra={"updated": updated})
    return {"status": "success", "updated": updated}
```

Em `app/workers/worker.py`, adicionar `sync_pending_checkouts_worker` ao import de `.tasks`. Em `functions=[...]`:

```python
            func(
                sync_pending_checkouts_worker,
                name="workers:tasks.sync_pending_checkouts_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=1,
            ),
```

Em `cron_jobs=[...]`:

```python
            cron(
                sync_pending_checkouts_worker,
                name="workers:tasks.sync_pending_checkouts_worker",
                minute=set(range(0, 60, 5)),
            ),
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_sync_checkout_status_use_case.py tests/test_sync_pending_checkouts_worker.py -v` e depois `python -m pytest -q`
Expected: PASS.

A consulta `list_pending_checkouts` não tem teste com banco (a suíte não usa Postgres); ela é validada na Task 12, cenário 3.

- [ ] **Step 7: Commit**

```bash
git add app/application/repositories/payment_repo.py app/infra/repo/payment_repo.py app/application/use_cases/sync_checkout_status.py app/workers/tasks.py app/workers/worker.py tests/test_sync_checkout_status_use_case.py tests/test_sync_pending_checkouts_worker.py
git commit -m "feat(workers): sync pending Mercado Pago checkouts to apply payment and expiration"
```

---

### Task 12: Documentação, homologação e virada

**Files:**
- Modify: `docs/INTEGRATION.md`, `docs/Webhooks.md`, `docs/API.md`, `runbooks/Falha_Gateway.md`
- Create: `docs/agent_memory/mercadopago-adapter-contract.md`

- [ ] **Step 1: Atualizar a documentação**

1. `docs/INTEGRATION.md`:
   - Trocar "cria um checkout Asaas" por "cria um checkout no gateway padrão (Mercado Pago)".
   - Acrescentar: "Com Mercado Pago, a vigência mínima efetiva do checkout é 30 minutos e `expired_url` não é usado (não há redirect de expiração)".
   - Trocar a tabela de eventos por uma que tenha também a coluna "Tópico Mercado Pago":
     - `payment` aprovado → `paid`;
     - expiração pelo cron → `expired`;
     - `payment` refunded → `refunded`.
   - No Go-live, trocar "Configure no Asaas" por "Configure os tópicos **Pagamentos** e **Planos e assinaturas** em Suas integrações > Webhooks, apontando para `/v1/webhooks/mercadopago`".
   - Em assinaturas, acrescentar: "`checkout_url` do job é o link em que o pagador autoriza o cartão; a assinatura só fica ativa na primeira fatura aprovada (cerca de 1 h depois da autorização)".
2. `docs/Webhooks.md`: seção nova "Webhook recebido do Mercado Pago" com a rota, os headers `x-signature`/`x-request-id`, o manifest, a variável `MERCADOPAGO_WEBHOOK_SECRET`, os três tópicos e o fato de o worker buscar o recurso na API.
3. `docs/API.md`: trocar "Criar/Consultar clientes no Asaas" por "Criar/Consultar clientes no gateway padrão".
4. `runbooks/Falha_Gateway.md`: incluir o Mercado Pago em sintomas, `MercadoPagoAPIError_<status>` nos jobs, o cron `sync_pending_checkouts_worker` e o rollback (`DEFAULT_GATEWAY_PROVIDER=asaas`).
5. `docs/agent_memory/mercadopago-adapter-contract.md`: registrar os fatos que não se deduzem do código:
   - o pagamento provisório usa o id do preapproval;
   - o vocabulário Asaas é intencional no adapter;
   - `source_event_id` é `<recurso>:<id>:<status>`;
   - o cron existe porque o Mercado Pago não notifica expiração;
   - os resultados (a)–(g) da Task 0.

- [ ] **Step 2: Commit**

```bash
git add docs/INTEGRATION.md docs/Webhooks.md docs/API.md runbooks/Falha_Gateway.md docs/agent_memory/mercadopago-adapter-contract.md
git commit -m "docs: Mercado Pago as default gateway, webhooks and operations"
```

- [ ] **Step 3: Homologação em staging (manual, com evidência no PR)**

Staging com `DEFAULT_GATEWAY_PROVIDER=mercadopago`, credenciais `TEST-` e webhooks da aplicação de teste. Para cada cenário, anexar `job_id`, `webhook_events.event_id` e a entrega interna:

1. `POST /v1/payments` com Pix → pagar → `GET /v1/payments/{id}` fica `paid` → Marketfy recebe `PAYMENT_STATUS_UPDATED` com `paid`.
2. O mesmo com cartão de teste aprovado.
3. Checkout não pago → depois de `expiration_date_to` + 5 min o cron marca `expired` → repetir `POST /v1/payments` com o mesmo `system_payment_id` gera um `init_point` novo (renovação).
4. Estornar no painel um checkout pago → `refunded`.
5. `POST /v1/customers` → `POST /v1/subscriptions` → o job devolve `checkout_url` = `init_point` → autorizar com o comprador de teste → fatura aprovada → assinatura `active`, **um único** pagamento `paid` com `provider_payment_id` = id do pagamento Mercado Pago.
6. `POST /v1/subscriptions/{id}/cancel` → preapproval `cancelled` → a notificação `subscription_preapproval` não gera segunda entrega (mesmo `dedupe_key`).
7. Uma assinatura Asaas existente segue recebendo `/v1/webhooks/asaas` normalmente.
8. Notificação com `x-signature` alterado → 401.

- [ ] **Step 4: Virada em produção**

1. Deploy de API e worker com `DEFAULT_GATEWAY_PROVIDER=asaas`, `MERCADOPAGO_*` de produção (`APP_USR-`) e webhooks de produção configurados. Nada muda para os consumidores.
2. Rodar `scripts/preflight_production_check.py` e `scripts/post_deploy_smoke.py`.
3. Trocar para `DEFAULT_GATEWAY_PROVIDER=mercadopago` e reiniciar API e worker.
4. Monitorar por 48 h: dead letters de `create_checkout_worker`/`process_gateway_notification`, `gateway_operations` em `requires_reconciliation`, pagamentos `pending` do gateway `mercadopago` com mais de 1 h e entregas internas falhas.
5. **Rollback:** voltar `DEFAULT_GATEWAY_PROVIDER=asaas`. Os registros já criados no Mercado Pago continuam sendo processados pelo adapter, pela rota de webhook e pelo cron.

---

## Fora de escopo (achados durante a análise)

- **Migrar customers já vinculados ao Asaas:** eles continuam criando assinaturas no Asaas.
- **Bug pré-existente na reconciliação:** `reconcile_gateway_operations_worker` (`app/workers/tasks.py`, ramo `create_subscription`) usa `try/except NotFoundError` em `payment_repo.get_by_provider_id`, que devolve `None` e nunca levanta. Por isso o laço nunca cria pagamentos ausentes. Isso vale para os dois gateways e deve ser corrigido em uma tarefa separada.
- **Código morto:** `ReconcilePayment` (`app/application/use_cases/reconcile_payment.py`) não é usado; só `apply_gateway_payment_status` é.
- **Colisão de customer entre sistemas:** o mesmo e-mail no Mercado Pago (ou o mesmo CPF no Asaas) em dois sistemas gera o mesmo `provider_customer_id` e bate em `uq_customers_provider_ref`. O comportamento é igual ao do Asaas hoje.
- **CI:** `.github/workflows/tests.yml` não define `DATABASE_URL`/`ASAAS_API_TOKEN`. A Task 0 resolve com `setdefault` no `conftest.py`; confirmar que o CI volta a coletar os testes.
