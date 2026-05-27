# Auditoria de Segurança e Arquitetura — Billing Core
## Foco: Fluxo de Assinaturas Recorrentes via Asaas

**Data:** 2026-05-27  
**Auditor:** Claude Sonnet 4.6 (Engenheiro de Software Sênior)  
**Escopo:** Billing Core — fluxo completo de assinaturas recorrentes, webhooks, pagamentos, cancelamentos, workers, segurança e aderência à documentação oficial Asaas  
**Branch auditada:** `main` (commit `a9ec031`)

---

## Índice

1. [Resumo Executivo](#resumo-executivo)
2. [Arquitetura Geral](#arquitetura-geral)
3. [Fluxo de Assinatura — Análise Completa](#fluxo-de-assinatura)
4. [O Que Está Correto](#o-que-está-correto)
5. [Bloqueadores Críticos](#bloqueadores-críticos)
6. [Problemas por Severidade](#problemas-por-severidade)
7. [Divergências com a Documentação Oficial do Asaas](#divergências-com-asaas)
8. [Riscos de Produção](#riscos-de-produção)
9. [Roadmap de Correção — PRs](#roadmap-de-correção)
10. [Veredito Final](#veredito-final)

---

## Resumo Executivo

O Billing Core apresenta uma **base arquitetural sólida** com separação de camadas (Clean Architecture + DDD), idempotência em múltiplos níveis, tratamento de transações com UoW, proteção contra replay de webhooks e infraestrutura de observabilidade. No entanto, existem **6 bugs críticos confirmados** que causarão falhas em produção real com dinheiro real, incluindo uma inconsistência de schema entre migration e modelo ORM que quebra toda a entrega de webhooks internos para pagamentos avulsos, uma falha de idempotência em `create_customer` que explode com erro 500 em chamadas duplicadas, e a ausência de tratamento dos eventos `PAYMENT_CONFIRMED` e `PAYMENT_DELETED` para pagamentos vinculados a assinaturas — criando buracos no lifecycle de assinaturas por cartão de crédito.

**Veredito: NOT PRODUCTION READY**. Após os fixes listados, passa para **Production Ready With Fixes**.

---

## Arquitetura Geral

```
┌─────────────────────────────────────────────────────────┐
│  Web Layer (FastAPI)                                      │
│  routes: /v1/subscriptions, /v1/payments, /v1/webhooks   │
│  deps: auth, idempotency, rate_limit                      │
├─────────────────────────────────────────────────────────┤
│  Application Layer                                        │
│  use_cases: CreateSubscription, ProcessWebhook,           │
│             CancelSubscription, ReconcilePayment,         │
│             CreateCustomer, CreatePayment, etc.           │
│  DTOs, Interfaces (Ports)                                 │
├─────────────────────────────────────────────────────────┤
│  Domain Layer                                             │
│  entities: Subscription, Payment, Customer,               │
│            GatewayOperation, WebhookEvent                 │
│  value_objects: CPF, CNPJ, Email                          │
│  enums, errors                                            │
├─────────────────────────────────────────────────────────┤
│  Infrastructure Layer (Adapters)                          │
│  asaas_provider.py  → Asaas REST API                      │
│  repos (SQLAlchemy async)                                 │
│  UoW (AsyncSession)                                       │
│  internal_webhook (httpx)                                 │
├─────────────────────────────────────────────────────────┤
│  Workers (ARQ + Redis)                                    │
│  create_subscription_worker                               │
│  process_webhook                                          │
│  cancel_subscription_worker                               │
│  reconcile_pending_payment_worker                         │
│  send_internal_webhook                                    │
└─────────────────────────────────────────────────────────┘
           │                          │
     PostgreSQL                     Redis
```

---

## Fluxo de Assinatura

### Fluxo esperado (documentação Asaas)

```
caller → POST /v1/subscriptions
       → worker: create_subscription_worker
         → gateway.create_customer (idempotente por CPF/CNPJ)
         → gateway.create_subscription (CREDIT_CARD)
         → gateway.get_subscription_payment
         → salva Subscription (status=PENDING)
         → salva Payment (status=PENDING, com checkout_url)
       → retorna job_id + checkout_url ao caller

customer → acessa checkout_url → insere dados do cartão → Asaas processa

Asaas → POST /v1/webhooks/asaas (PAYMENT_CONFIRMED)  ← não tratado para sub!
Asaas → POST /v1/webhooks/asaas (PAYMENT_RECEIVED)
       → worker: process_webhook
         → sub.mark_as_paid(payment_date)
         → sub.status = ACTIVE
         → sub.expires_at += billing_period
         → salva Payment (status=PAID)
         → dispara internal webhook ao caller

(ciclos seguintes)
Asaas → cobra automaticamente → envia PAYMENT_RECEIVED
       → mesmo fluxo → extends expires_at
```

### O que o código faz (conforme implementado)

| Etapa | Status | Arquivo | Observação |
|-------|--------|---------|------------|
| Autenticação interna (X-System + X-API-Key) | ✅ Correto | `security.py` | HMAC compare_digest |
| Validação de idempotência por Idempotency-Key | ✅ Correto | `idempotency.py` | SHA256 + Redis TTL 24h |
| Criação de customer no Asaas | ✅ Correto (gateway) | `asaas_provider.py:246` | Busca por CPF/CNPJ primeiro |
| Criação de customer local | ❌ Bug crítico | `create_customer.py:48` | Sem check local de duplicata |
| Criação de assinatura (billing_type) | ⚠️ Limitação | `create_subscription.py:89` | Hardcoded CREDIT_CARD |
| externalReference na assinatura | ❌ Ausente | `asaas_provider.py:132` | Não enviado ao Asaas |
| Subscription começa como PENDING | ✅ Correto | `create_subscription.py:96` | Não ativa antes do pagamento |
| checkout_url retornada | ✅ Correto | `create_subscription.py:136` | Usa invoiceUrl do payment |
| Webhook PAYMENT_RECEIVED ativando assinatura | ✅ Correto | `process_webhook.py:107` | mark_as_paid + expires_at |
| Webhook PAYMENT_CONFIRMED para assinatura | ❌ Bug | `process_webhook.py:172` | Silently dropped, sem save |
| Webhook PAYMENT_DELETED para assinatura | ❌ Bug | `process_webhook.py:172` | Silently dropped, sem save |
| Idempotência de webhook (webhook_events) | ✅ Correto | `process_webhook.py:29` | Por event_id único |
| Replay protection (SHA256 + Redis) | ✅ Correto | `security.py:125` | TTL 5 min |
| Lock de processamento por evento | ✅ Correto | `tasks.py:129` | Redis SET NX por event_id |
| Recorrência delegada ao Asaas | ✅ Correto | — | Não reimplementada |
| Cancelamento com idempotência | ✅ Correto | `cancel_subscription.py` | GatewayOperation + UoW |
| Retry de Asaas 5xx | ❌ Bug | `tasks.py:289` | AsaasAPIError não re-raise |
| Entrega de internal webhook (pagamentos avulsos) | ❌ Bug crítico | `tasks.py:98`, migration 000003 | subscription_id NOT NULL no DB |
| SELECT FOR UPDATE em PAYMENT_RECEIVED | ⚠️ Ausente | `process_webhook.py:108` | Race condition possível |
| Reconciliação automática de REQUIRES_RECONCILIATION | ⚠️ Ausente | — | Requer intervenção manual |

---

## O Que Está Correto

### Segurança
- ✅ Autenticação S2S com `hmac.compare_digest` (resistente a timing attacks) — `security.py:65`
- ✅ Webhook Asaas validado via `asaas-access-token` com compare_digest — `security.py:106`
- ✅ Proteção contra replay attack: SHA256 do body + Redis TTL 5 minutos — `security.py:125`
- ✅ Lock Redis por event_id previne processamento concorrente do mesmo webhook — `tasks.py:129`
- ✅ Validação de CORS wildcard bloqueada em produção — `config.py:74`
- ✅ Endpoint Asaas sandbox bloqueado em produção — `config.py:71`
- ✅ Secrets com mínimo 32 chars e sem espaços — `config.py:83`
- ✅ ALLOWED_INTERNAL_WEBHOOK_HOSTS obrigatório em produção — `config.py:92`
- ✅ Assinatura HMAC-SHA256 para webhooks internos outbound — `internal_webhook.py:40`
- ✅ DEBIT_CARD bloqueado para assinaturas (Asaas não suporta) — `asaas_provider.py:129`

### Arquitetura
- ✅ Clean Architecture com separação clara de camadas (Domain → Application → Infra → Web)
- ✅ UoW (Unit of Work) com boundaries transacionais corretos
- ✅ Repositórios como abstrações — domínio não conhece SQLAlchemy
- ✅ GatewayOperation como mecanismo de idempotência para operações de gateway
- ✅ Rollback + marcação REQUIRES_RECONCILIATION em falhas de sincronização local — `create_subscription.py:153`
- ✅ CANCELLED state com `request_cancellation` → `cancel` correto — `subscription.py:119`

### Fluxo Financeiro
- ✅ Assinatura **não é ativada** antes de `PAYMENT_RECEIVED` — `process_webhook.py:107`
- ✅ `assinatura criada ≠ pagamento realizado` está corretamente implementado
- ✅ Recorrência delegada inteiramente ao Asaas (sem reimplementação interna)
- ✅ `expires_at` calculado com aritmética de calendário correta (meses com dias variáveis) — `subscription.py:13`
- ✅ `mark_as_paid` leva em conta `trial_ends_at` via `max(payment_date, expires_at)` — `subscription.py:104`
- ✅ SUBSCRIPTION_DELETED e SUBSCRIPTION_INACTIVATED processados corretamente — `process_webhook.py:153`

### Infraestrutura
- ✅ Processamento assíncrono via ARQ com dead letter queue — `worker.py`
- ✅ Rate limiting independente para rotas internas (60/min) e webhooks (120/min)
- ✅ Logging estruturado JSON com correlation IDs
- ✅ Timeout configurável no DB (`DB_STATEMENT_TIMEOUT_MS`) e nos jobs (`WORKER_JOB_TIMEOUT_SECONDS`)
- ✅ Idempotência em dois níveis: Redis (Idempotency-Key) + DB (GatewayOperation.dedupe_key)
- ✅ Checklist e runbooks operacionais existentes (`docs/`, `runbooks/`)

---

## Bloqueadores Críticos

### BUG-C1: Schema mismatch — `internal_webhook_deliveries.subscription_id` NOT NULL no DB mas nullable no ORM

**Arquivo:** `alembic/versions/20260424_000003_gateway_ops_and_internal_deliveries.py:57` vs `app/infra/db/models/internal_webhook_delivery.py:25`

**Evidência:**
```python
# Migration 000003 (o que está no DB real):
sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False)  # NOT NULL

# ORM Model (o que o código assume):
subscription_id: Mapped[UUID | None] = mapped_column(..., nullable=True)  # nullable

# tasks.py:98 — payment-only delivery:
return InternalWebhookDelivery(
    ...
    subscription_id=None,   # CRASH: violação de NOT NULL no DB
    payment_id=payment.id,
)
```

**Impacto:** Toda entrega de webhook interno para eventos `PAYMENT_STATUS_UPDATED` (pagamentos avulsos) falha com `IntegrityError: null value in column "subscription_id" violates not-null constraint`. O worker vai para dead letter após 3 tentativas. O sistema de pagamento do chamador **nunca recebe confirmação** de status de pagamento avulso.

**Severidade:** CRÍTICO — falha silenciosa em produção, perda de notificação financeira.

---

### BUG-C2: `uq_payments_system_ref` — constraint no DB é single-column, ORM espera compound

**Arquivo:** `alembic/versions/20260424_000001_initial_schema.py:94` vs `app/infra/db/models/payment.py:19`

**Evidência:**
```python
# Migration 000001 (DB real):
sa.UniqueConstraint("system_payment_id", name="uq_payments_system_ref")
# Constraint GLOBAL em system_payment_id sozinho

# ORM Model:
UniqueConstraint("system_payment_id", "from_system", name="uq_payments_system_ref")
# Constraint POR SISTEMA (compound)
```

**Impacto:** Se dois sistemas distintos criarem pagamentos com o mesmo `system_payment_id` (ex: ambos usam "payment-001"), o segundo INSERT falha com `UniqueViolationError`. Em um ambiente multi-sistema real, essa colisão **vai acontecer**. Os `system_payment_id` são IDs do sistema de origem, não do Billing Core — é esperado que sejam repetidos entre sistemas.

**Severidade:** CRÍTICO — falha em produção multi-sistema com erros 500 inesperados.

---

### BUG-C3: `PAYMENT_CONFIRMED` e `PAYMENT_DELETED` para assinaturas — silently dropped sem salvar webhook_event

**Arquivo:** `app/application/use_cases/process_webhook.py:172`

**Evidência:**
```python
# process_webhook.py — o código percorre os blocos na ordem:
if payload.details.id and not payload.details.subscription:
    # SKIPPED: subscription_id está presente

if payload.event in (EventType.UNKNOWN, EventType.PAYMENT_OVERDUE,
                     EventType.PAYMENT_CHARGEBACK_REQUESTED, EventType.PAYMENT_REFUNDED):
    # SKIPPED: PAYMENT_CONFIRMED e PAYMENT_DELETED não estão nessa lista

if payload.event == EventType.PAYMENT_RECEIVED and ...:
    # SKIPPED: evento é PAYMENT_CONFIRMED ou PAYMENT_DELETED

if payload.event in [EventType.SUBSCRIPTION_DELETED, EventType.SUBSCRIPTION_INACTIVATED]:
    # SKIPPED

return None  # ← webhook_event NUNCA salvo, NUNCA processado
```

**Impacto:**
- `PAYMENT_CONFIRMED` com `subscription_id`: para **cartão de crédito**, é o evento de autorização — o pagamento existe no gateway mas nunca é registrado localmente. A assinatura fica em PENDING enquanto o dinheiro já foi autorizado.
- `PAYMENT_DELETED` com `subscription_id`: se um pagamento vinculado à assinatura for deletado no Asaas, o status local nunca é atualizado.
- **Ausência de deduplicação**: como `webhook_event` nunca é salvo, se o Asaas reentregas o evento por qualquer motivo, ele será processado do zero novamente — sem idempotência.

**Severidade:** CRÍTICO — lifecycle incompleto de assinaturas por cartão de crédito.

---

### BUG-C4: `create_customer` não é idempotente localmente — segundo call explode com DB constraint

**Arquivo:** `app/application/use_cases/create_customer.py:40`

**Evidência:**
```python
# 1ª chamada com mesmo system_customer_id:
response = await gateway.create_customer(cpfCnpj=cpf, ...)  # busca ou cria no gateway OK
cus.bind_provider_customer(response.cus_id)
await self.repo.save(cus)   # INSERT bem-sucedido
await self.uow.commit()

# 2ª chamada com mesmo system_customer_id:
response = await gateway.create_customer(cpfCnpj=cpf, ...)  # encontra existente no gateway OK
cus.bind_provider_customer(response.cus_id)
await self.repo.save(cus)   # INSERT → UniqueViolationError em uq_customers_system_ref!
# → exception não tratada → HTTP 500
```

**Impacto:** Toda tentativa de criar um customer já existente retorna 500. A operação é sincronizada (não async/worker), portanto o chamador recebe erro imediatamente. Sem retentativa controlada, o chamador tenta de novo e volta a falhar. O gateway já tem o customer criado, mas o local nunca é sincronizado.

**Severidade:** CRÍTICO — `POST /v1/customers` não é idempotente apesar da documentação afirmar que é.

---

### BUG-C5: `AsaasAPIError` capturado antes do handler genérico sem re-raise — erros 5xx do Asaas não são retentados

**Arquivo:** `app/workers/tasks.py:289` (create_subscription_worker), `:399` (create_payment_worker), `:680` (cancel_subscription_worker)

**Evidência:**
```python
# Todos os workers seguem esse padrão:
except AsaasAPIError as exc:
    await update_job_metadata(..., status="failed", ...)
    await register_dead_letter(...)
    return {"status": "failed", "error": str(exc)}   # ← RETURN, não RAISE

# Mas o handler genérico abaixo re-raise corretamente para ARQ retry:
except Exception as exc:
    is_final_try = job_try >= settings.WORKER_MAX_TRIES
    ...
    raise   # ← ARQ vai agendar retry
```

**Impacto:** Quando o Asaas retorna 500, 502, 503, ou 504 (indisponibilidade transitória), o job é marcado como `failed` permanentemente e vai para dead letter após a primeira tentativa. ARQ nunca agenda retry porque o worker retornou ao invés de lançar. Qualquer instabilidade momentânea do Asaas **falha permanentemente** todas as criações/cancelamentos em voo.

**Observação adicional:** Em `create_payment_worker`, a variável `is_client_error = 400 <= exc.status_code < 500` é computada mas nunca usada — código morto que indica intenção mas não execução.

**Severidade:** CRÍTICO — zero resiliência a falhas transitórias do gateway.

---

### BUG-C6: `Payment.__init__` não guarda `net_value=None` — crash com TypeError ao carregar do DB

**Arquivo:** `app/domain/entities/payment.py:51` vs `app/infra/db/models/payment.py:32`

**Evidência:**
```python
# Coluna no DB (migration 000001):
sa.Column("net_value", sa.DECIMAL(precision=18, scale=2), nullable=True)  # pode ser NULL

# PaymentModel.to_domain():
net_value=self.net_value,  # se DB tem NULL → passa None

# Payment.__init__:
net_value: Decimal = Decimal(0),  # default é Decimal(0), mas None pode ser passado explicitamente
...
if net_value < 0:   # TypeError: '<' not supported between 'NoneType' and 'int'
    raise DomainError(...)
```

**Impacto:** Qualquer payment com `net_value=NULL` no banco (linhas de migração antiga ou edge case) causa `TypeError` ao ser carregado. Todo o fluxo que carrega esse pagamento — incluindo process_webhook, reconcile_payment e list endpoints — crasha.

**Severidade:** CRÍTICO (latente) — não dispara para payments criados via código atual (default Decimal(0)), mas afeta dados históricos ou migrados.

---

## Problemas por Severidade

### Alta Severidade

#### RISK-H1: Ausência de SELECT FOR UPDATE no PAYMENT_RECEIVED — race condition em ativação de assinatura

**Arquivo:** `app/application/use_cases/process_webhook.py:108`

```python
sub = await self.sub_repo.get_by_provider_id(payload.details.subscription)
# ← sem WITH FOR UPDATE
sub.mark_as_paid(payment_date=payment_date)  # extends expires_at
sub = await self.sub_repo.save(sub)
```

Se dois webhooks PAYMENT_RECEIVED para a mesma assinatura chegarem simultaneamente (ex: retry do Asaas + processamento tardio), ambos podem ler `sub` em PENDING, ambos chamam `mark_as_paid`, e `expires_at` é estendido duas vezes. O lock Redis protege o **mesmo** `event_id`, mas não eventos distintos que afetam a mesma assinatura.

**Fix:** Usar `sub_repo.get_by_id_for_update` (já implementado para outros fluxos) no handler de PAYMENT_RECEIVED.

---

#### RISK-H2: Sem worker de reconciliação automática para operações REQUIRES_RECONCILIATION

**Arquivo:** `app/application/use_cases/create_subscription.py:156`, `cancel_subscription.py:113`

Quando a assinatura é criada no Asaas mas a sincronização local falha, a `GatewayOperation` fica em `REQUIRES_RECONCILIATION`. Não existe job que varra essas operações e as resolva automaticamente. O operador precisa intervir manualmente — mas não há dashboard, runbook ou script para isso.

**Fix:** Worker periódico ou endpoint administrativo para reconciliação.

---

#### RISK-H3: Subscription com PAYMENT_CONFIRMED por cartão de crédito fica em PENDING até settlement

**Arquivo:** `app/application/use_cases/process_webhook.py` (ausência de handler)

Para cartão de crédito no Brasil, o fluxo Asaas é: `PENDING → CONFIRMED (autorizado) → RECEIVED (liquidado)`. Entre CONFIRMED e RECEIVED pode haver minutos a horas. Durante esse período, o cliente tem o cartão cobrado mas a assinatura permanece PENDING, sem acesso ao serviço. Isso impacta diretamente a experiência do usuário e pode gerar chargebacks por percepção de cobrança sem serviço.

**Fix:** Tratar `PAYMENT_CONFIRMED` no handler de assinaturas para atualizar o status do payment para CONFIRMED (sem ativar a assinatura ainda) e registrar o evento.

---

### Média Severidade

#### RISK-M1: `externalReference` não enviado na criação de assinatura no Asaas

**Arquivo:** `app/infra/interfaces/asaas_provider.py:132`

O payload de criação não inclui `externalReference`. Sem ele, é impossível reconciliar assinaturas diretamente no painel Asaas com registros locais — especialmente crítico em casos de suporte, auditoria financeira e reconciliação manual.

```python
payload = {
    "customer": customer_provider_id,
    "billingType": billing_type.value,
    "value": float(value),
    "nextDueDate": next_due_date.isoformat(),
    "cycle": cycle.value,
    "description": description,
    # ← externalReference ausente
}
```

---

#### RISK-M2: `billing_type` hardcoded como `CREDIT_CARD` para todas as assinaturas

**Arquivo:** `app/application/use_cases/create_subscription.py:89`

```python
billing_type=PaymentType.CREDIT_CARD,  # hardcoded
```

`CreateSubscriptionDTO` não tem campo `billing_type`. Assinaturas por PIX ou boleto são impossíveis. O Asaas suporta todos os três. Isso é uma decisão de design não documentada que limita o produto e pode surpreender integradores.

---

#### RISK-M3: Cobertura de testes extremamente baixa

**Arquivo:** `tests/`

Apenas 3 testes para `create_subscription`. Zero testes para:
- `process_webhook` (o fluxo mais crítico)
- `cancel_subscription`
- `reconcile_payment`
- `create_customer`
- Cenários de race condition
- Cenários de retry/dead letter
- Cenários de PAYMENT_CONFIRMED/DELETED para assinaturas

A checklist de produção exige "suite de testes verde", mas a suite atual não valida os caminhos críticos.

---

#### RISK-M4: `order_by(paid_date.desc())` em lista de pagamentos da assinatura — NULL ordering inesperado

**Arquivo:** `app/infra/repo/payment_repo.py:58`

```python
stmt = select(PaymentModel).where(...).order_by(PaymentModel.paid_date.desc())
```

No PostgreSQL, `DESC` coloca NULLs primeiro (NULLS FIRST). Pagamentos PENDING (sem `paid_date`) aparecem antes dos pagamentos PAID/RECEIVED. Isso pode fazer `_build_existing_response` retornar o pagamento errado para a resposta idempotente.

**Fix:** `order_by(PaymentModel.paid_date.desc().nulls_last())` ou usar `created_at` como tiebreaker.

---

#### RISK-M5: `expires_at` passado pelo caller sem semântica clara documentada

**Arquivo:** `app/web/schemas/subscription.py:73`, `app/domain/entities/subscription.py:104`

O campo `expires_at` deve ser no futuro (validado), mas seu valor inicial afeta diretamente o cálculo de expiração pós-pagamento via `max(payment_date, self.expires_at)`. Se um caller enviar uma data muito distante no futuro (ex: 5 anos), após o primeiro pagamento, `expires_at = 5_anos_no_futuro + 1_mes`. Isso viola o contrato esperado de "mensalidade mensal".

Não há documentação que esclareça o contrato correto do campo para callers.

---

### Baixa Severidade

#### RISK-L1: Código morto em `create_subscription.py` — `existing_operation or GatewayOperation(...)`

**Arquivo:** `app/application/use_cases/create_subscription.py:64-77`

O bloco `if existing_operation:` sempre levanta `DomainError`, portanto a linha seguinte `operation = existing_operation or GatewayOperation(...)` sempre executa com `existing_operation = None`. O `existing_operation or` é morto. Não é um bug, mas gera confusão para leitores do código.

---

#### RISK-L2: `httpx.AsyncClient` instanciado por chamada sem fechar — resource leak

**Arquivo:** `app/infra/interfaces/internal_webhook.py:24`

```python
class InternalWebhookProvider(InternalWebhook):
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)  # nunca fechado
```

Cada invocação de `send_internal_webhook` cria um novo `InternalWebhookProvider` com um `httpx.AsyncClient` que nunca é explicitamente fechado. Em produção, isso causa acúmulo de conexões TCP abertas. O cliente deve ser usado como context manager ou fechado em um método de cleanup.

---

#### RISK-L3: `payment_date` tratado como `datetime` no path direto e como `date` no `apply_gateway_payment_status`

**Arquivo:** `app/application/use_cases/reconcile_payment.py:20` vs `process_webhook.py:136`

```python
# process_webhook.py (direto):
payment.mark_as_paid(payment_date=payment_date, ...)  # payment_date é datetime

# apply_gateway_payment_status:
dt = datetime.combine(payment_date, time.min, tzinfo=timezone.utc)  # payment_date é date
```

O tipo difere entre os dois caminhos. No path direto, a hora exata do webhook é preservada. No path via reconcile, é truncada para meia-noite UTC. Inconsistência de precisão em registros financeiros.

---

#### RISK-L4: `SendSubInternalWebhook` use case — potencialmente legado/não usado no fluxo principal

**Arquivo:** `app/application/use_cases/send_sub_internal_webhook.py`

O use case `SendSubInternalWebhook` envia webhooks internos diretamente, sem passar por `InternalWebhookDelivery` (sem persistência, sem retry, sem deduplicação). O fluxo principal em `tasks.py` usa `_build_internal_delivery` que persiste no banco. Este use case parece ser código legado que não é mais invocado pelo worker principal. Se for removido, isso não impacta o fluxo atual, mas se for acidentalmente chamado, bypassa todas as garantias de entrega.

---

## Divergências com a Documentação Oficial do Asaas

| # | Divergência | Impacto |
|---|-------------|---------|
| DIV-1 | `PAYMENT_CONFIRMED` para assinaturas não tratado. Asaas envia esse evento para cartão de crédito (autorização). | Alto — assinatura fica PENDING após autorização do cartão |
| DIV-2 | `PAYMENT_DELETED` para assinaturas não tratado. Asaas envia quando um pagamento é deletado. | Médio — status local diverge do gateway |
| DIV-3 | `externalReference` não enviado na criação de assinaturas. Asaas recomenda para reconciliação. | Médio — dificulta suporte e auditoria |
| DIV-4 | Deduplicação de `PAYMENT_CONFIRMED` e `PAYMENT_DELETED` para assinaturas não funciona porque `webhook_event` nunca é salvo para esses eventos. | Alto — violação da garantia at-least-once do Asaas |
| DIV-5 | Asaas suporta BOLETO, CREDIT_CARD e PIX para assinaturas recorrentes. Sistema limita a CREDIT_CARD. | Médio — limitação de produto não documentada |
| DIV-6 | Asaas retorna status `ACTIVE`/`INACTIVE`/`EXPIRED` para assinaturas. O sistema não mapeia `INACTIVE` e `EXPIRED` para status locais adequados. | Baixo — pode impactar reconciliação futura |
| DIV-7 | Fila de webhooks Asaas é pausada após 15 respostas não-200 consecutivas. O sistema sempre retorna 200, portanto não pausa, mas 500s no endpoint de webhook contariam. Não há alerta para esse cenário. | Baixo — risco operacional em produção |

---

## Riscos de Produção

### Risco de Perda Financeira

| Cenário | Mecanismo | Probabilidade | Impacto |
|---------|-----------|---------------|---------|
| Asaas tem instabilidade momentânea (5xx) | BUG-C5: AsaasAPIError não é retentado | Alta (Asaas tem SLA de 99.5%) | Todas as criações de assinatura em voo falham permanentemente |
| Chamador chama `create_customer` duas vezes | BUG-C4: sem idempotência local | Alta (retry é padrão) | HTTP 500, customer nunca sincronizado |
| Pagamento avulso confirmado | BUG-C1: subscription_id NOT NULL | Certeza | Internal webhook nunca entregue, caller não sabe que pagamento foi confirmado |
| Dois sistemas com mesmo system_payment_id | BUG-C2: unique constraint global | Média | Segundo sistema não consegue criar pagamentos |

### Risco de Acesso Indevido

| Cenário | Mecanismo | Probabilidade | Impacto |
|---------|-----------|---------------|---------|
| Assinatura ativada sem pagamento | Não identificado | Baixa | Sistema nunca ativa sem PAYMENT_RECEIVED ✅ |
| Webhook falso ativando assinatura | Token validado com compare_digest | Baixa | Mitigado ✅ |
| Replay de webhook antigo | SHA256 + Redis TTL 5min + webhook_events | Muito baixa | Dupla proteção ✅ |

### Risco de Inconsistência de Dados

| Cenário | Mecanismo | Probabilidade | Impacto |
|---------|-----------|---------------|---------|
| PAYMENT_RECEIVED duplicado | RISK-H1: sem SELECT FOR UPDATE | Média (Asaas at-least-once) | expires_at duplicado na assinatura |
| Assinatura em REQUIRES_RECONCILIATION | Sem worker automático | Alta (qualquer falha transient) | Assinatura presa em estado inválido indefinidamente |
| Pagamento por cartão CONFIRMED mas sub em PENDING | BUG-C3 | Alta para CC | Divergência entre gateway e local |

---

## Roadmap de Correção

### PR-1 — CRÍTICO: Fix schema migration `internal_webhook_deliveries` + `uq_payments_system_ref`

**Estimativa:** 2h  
**Risco de deploy:** Requer migration cuidadosa em produção

**Arquivos:**
- `alembic/versions/` → nova migration alterando `subscription_id` para nullable
- `alembic/versions/` → nova migration recriando `uq_payments_system_ref` como compound

```python
# Nova migration:
def upgrade():
    # Fix 1: subscription_id nullable
    op.alter_column("internal_webhook_deliveries", "subscription_id", nullable=True)

    # Fix 2: recriar constraint como compound
    op.drop_constraint("uq_payments_system_ref", "payments")
    op.create_unique_constraint(
        "uq_payments_system_ref",
        "payments",
        ["system_payment_id", "from_system"]
    )
```

**Validação:** Executar `alembic upgrade head` em staging, verificar que payment-only internal webhook deliveries são salvas com sucesso.

---

### PR-2 — CRÍTICO: Fix `create_customer` idempotência local

**Estimativa:** 1h  
**Risco de deploy:** Baixo

**Arquivo:** `app/application/use_cases/create_customer.py`

```python
async def execute(self, dtos: CreateCustomerDTO, system: System, gateway_provider: GatewayProvider) -> str:
    # Verificar existência local ANTES de criar no gateway
    existing = await self.repo.get_by_system_id_and_system(
        dtos.system_customer_id, system
    )
    if existing and existing.has_provider_binding():
        return existing.provider_customer_id

    # ... resto do fluxo atual
```

**Também requer:** Método `get_by_system_id_and_system` no `CustomerRepository` e `CustomerRepositoryINFRA`.

---

### PR-3 — CRÍTICO: Fix `AsaasAPIError` — não retentar 4xx, retentar 5xx

**Estimativa:** 2h  
**Risco de deploy:** Baixo

**Arquivo:** `app/workers/tasks.py` (todos os workers)

```python
except AsaasAPIError as exc:
    is_client_error = 400 <= exc.status_code < 500
    if is_client_error:
        # Terminal: payload inválido, cliente inexistente
        await update_job_metadata(..., status="failed")
        await register_dead_letter(...)
        return {"status": "failed", "error": str(exc)}
    else:
        # Transitório (5xx): re-raise para ARQ agendar retry
        await update_job_metadata(
            ...,
            status="failed" if is_final_try else "retrying",
        )
        if is_final_try:
            await register_dead_letter(...)
        raise  # ← ARQ agenda retry
```

---

### PR-4 — CRÍTICO: Tratar `PAYMENT_CONFIRMED` e `PAYMENT_DELETED` para assinaturas

**Estimativa:** 3h  
**Risco de deploy:** Baixo

**Arquivo:** `app/application/use_cases/process_webhook.py`

```python
# Novo bloco para PAYMENT_CONFIRMED com subscription:
if payload.event == EventType.PAYMENT_CONFIRMED and payload.details.subscription:
    sub = await self.sub_repo.get_by_provider_id(payload.details.subscription)
    payment = await self.payment_repo.get_by_provider_id(payload.details.id)
    if payment:
        apply_gateway_payment_status(
            payment,
            "CONFIRMED",
            payload.details.payment_date.date() if payload.details.payment_date else None,
            payload.details.net_value,
        )
        await self.payment_repo.save(payment)
    event.mark_as_processed()
    await self.webhook_event_repo.save(event)
    await self.uow.commit()
    return ProcessWebhookResponse(
        event=InternalEventType.PAYMENT_STATUS_UPDATED,
        payment_id=payment.id if payment else None,
        subscription_id=sub.id,
    )

# Novo bloco para PAYMENT_DELETED com subscription:
if payload.event == EventType.PAYMENT_DELETED and payload.details.subscription:
    payment = await self.payment_repo.get_by_provider_id(payload.details.id)
    if payment and payment.payment_status in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:
        payment.mark_as_canceled()
        await self.payment_repo.save(payment)
    event.mark_as_processed()
    await self.webhook_event_repo.save(event)
    await self.uow.commit()
    return None
```

---

### PR-5 — CRÍTICO: Fix `net_value=None` crash no `Payment.__init__`

**Estimativa:** 30min  
**Risco de deploy:** Nenhum

**Arquivo:** `app/domain/entities/payment.py:51`

```python
# Antes:
if net_value < 0:
    raise DomainError("Pagamento nao pode ter valor liquido negativo.")

# Depois:
if net_value is not None and net_value < 0:
    raise DomainError("Pagamento nao pode ter valor liquido negativo.")
```

---

### PR-6 — ALTO: SELECT FOR UPDATE em PAYMENT_RECEIVED para evitar race condition

**Estimativa:** 1h  
**Risco de deploy:** Baixo

**Arquivo:** `app/application/use_cases/process_webhook.py:108`

```python
# Substituir:
sub = await self.sub_repo.get_by_provider_id(payload.details.subscription)
# Por:
sub = await self.sub_repo.get_by_provider_id_for_update(payload.details.subscription)
```

**Também requer:** Implementar `get_by_provider_id_for_update` com `SELECT ... FOR UPDATE` no `SubscriptionRepositoryINFRA`.

---

### PR-7 — ALTO: Adicionar worker de reconciliação para REQUIRES_RECONCILIATION

**Estimativa:** 4h  
**Risco de deploy:** Baixo (additive)

**Novo arquivo:** `app/workers/tasks.py` + novo job

```python
async def reconcile_gateway_operations_worker(ctx):
    """Varre gateway_operations em REQUIRES_RECONCILIATION e tenta resolver."""
    # 1. Buscar operações REQUIRES_RECONCILIATION criadas há mais de 5 min
    # 2. Para create_subscription: verificar se assinatura existe no Asaas e criar local
    # 3. Para cancel_subscription: verificar se assinatura está cancelada no Asaas
    # 4. Marcar como COMPLETED ou FAILED conforme resultado
```

**Scheduling:** Cron a cada 15 minutos ou endpoint administrativo protegido.

---

### PR-8 — MÉDIO: Adicionar `externalReference` na criação de assinatura + suporte a `billing_type`

**Estimativa:** 2h  
**Risco de deploy:** Nenhum (additive)

**Arquivo:** `app/infra/interfaces/asaas_provider.py:132`, `app/application/use_cases/create_subscription.py:86`

```python
# asaas_provider.py — adicionar externalReference:
payload = {
    ...
    "externalReference": f"billing:{system.value}:{system_sub_id}",
}

# create_subscription.py — billing_type como parâmetro:
gateway_subscription_id = await gateway.create_subscription(
    ...
    billing_type=request.billing_type or PaymentType.CREDIT_CARD,
    ...
)
```

---

### PR-9 — MÉDIO: Expandir cobertura de testes críticos

**Estimativa:** 8h  
**Risco de deploy:** Nenhum

Cenários mínimos a cobrir:
- `process_webhook`: PAYMENT_RECEIVED para assinatura (ativa, pendente, já ativa)
- `process_webhook`: PAYMENT_CONFIRMED para assinatura (após PR-4)
- `process_webhook`: SUBSCRIPTION_DELETED
- `process_webhook`: evento duplicado (idempotência)
- `create_customer`: chamada duplicada com mesmo system_customer_id (após PR-2)
- `cancel_subscription`: assinatura já cancelada (idempotente)
- `reconcile_payment`: status CONFIRMED → PAID
- Workers: `AsaasAPIError` 4xx vs 5xx retry behavior (após PR-3)

---

### PR-10 — BAIXO: Fixes menores e limpeza

**Estimativa:** 2h  
**Risco de deploy:** Nenhum

1. `payment_repo.py:58` — `order_by(paid_date.desc().nulls_last())`
2. `internal_webhook.py:24` — usar `httpx.AsyncClient` como context manager ou fechar em finalizer
3. `create_subscription.py:71` — remover dead code `existing_operation or ...`
4. `reconcile_payment.py:20` — padronizar `payment_date` como `datetime` nos dois paths
5. Documentar semântica de `expires_at` no schema e no DTO
6. Remover (ou documentar claramente como legacy) o use case `SendSubInternalWebhook`

---

## Resumo do Roadmap

| PR | Severidade | Esforço | Depende de | Bloqueia produção? |
|----|------------|---------|------------|-------------------|
| PR-1 | CRÍTICO | 2h | — | SIM |
| PR-2 | CRÍTICO | 1h | — | SIM |
| PR-3 | CRÍTICO | 2h | — | SIM |
| PR-4 | CRÍTICO | 3h | — | SIM (para CC) |
| PR-5 | CRÍTICO | 30min | — | SIM (latente) |
| PR-6 | ALTO | 1h | — | NÃO (race improvável inicialmente) |
| PR-7 | ALTO | 4h | — | NÃO (manual workaround existe) |
| PR-8 | MÉDIO | 2h | — | NÃO |
| PR-9 | MÉDIO | 8h | PR-1..5 | NÃO (mas obrigatório pré-GA) |
| PR-10 | BAIXO | 2h | — | NÃO |

**Total bloqueadores críticos:** ~8.5h de desenvolvimento + testes + deploy cuidadoso.

---

## Veredito Final

```
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   ❌  NOT PRODUCTION READY                               ║
║                                                          ║
║   Após correção dos PRs 1 a 5:                           ║
║   ✅  PRODUCTION READY WITH FIXES                        ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
```

### Justificativa

O sistema possui uma arquitetura correta e bem pensada. Os fundamentos de segurança são sólidos. O fluxo de ativação de assinatura pós-pagamento está correto — nunca ativa antes de confirmação real. A recorrência está devidamente delegada ao Asaas.

No entanto, **6 bugs críticos confirmados** impedem o uso em produção com dinheiro real:

1. **Schema mismatch** quebra toda entrega de webhook interno para pagamentos avulsos — o sistema de pagamento do chamador nunca recebe notificação de status.
2. **Constraint única global** em `payments.system_payment_id` vai quebrar qualquer ambiente com mais de um sistema consumidor.
3. **PAYMENT_CONFIRMED/DELETED** para assinaturas são silently dropped — assinaturas por cartão ficam em PENDING enquanto o dinheiro está autorizado.
4. **create_customer não idempotente** localmente — qualquer retry explode com 500.
5. **AsaasAPIError 5xx não retentado** — qualquer instabilidade do Asaas falha permanentemente jobs em voo.
6. **net_value=None** causa TypeError latente ao carregar payments antigos.

Esses bugs não são teóricos — vão ocorrer nos primeiros dias de produção com tráfego real.

---

*Auditoria gerada por Claude Sonnet 4.6 em 2026-05-27. Baseada em leitura completa do código-fonte, contratos de interface, migrations, workers, handlers de webhook e documentação oficial do Asaas.*
