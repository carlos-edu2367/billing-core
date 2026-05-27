# RELATÓRIO DE VALIDAÇÃO TÉCNICA — Billing Core

## Validação dos Bloqueadores Críticos pós-auditoria

**Data:** 2026-05-27
**Revisor:** Claude Sonnet 4.6 (Engenheiro Staff — Billing Systems)
**Branch:** `main` · Commits analisados: `a9ec031` → atual
**Fonte de verdade:** [Documentação oficial Asaas](https://docs.asaas.com/docs/payment-events) · [Subscription Events](https://docs.asaas.com/docs/subscription-events) · [Checkout Recorrente](https://docs.asaas.com/docs/checkout-with-subscription-recurring) · [Fluxos de Webhook](https://docs.asaas.com/docs/fluxos-de-webhook)

---

## 1. Resumo Executivo

Dos 6 bloqueadores críticos identificados na auditoria original,  **4 foram corrigidos** , **2 foram parcialmente corrigidos** e **7 novos riscos foram introduzidos** — incluindo um risco crítico de schema não identificado antes: a cadeia de migration downgrade está quebrada, o worker de reconciliação tem falhas de concorrência multi-worker, e há uma divergência fundamental com a documentação oficial do Asaas que torna o ciclo de vida de assinaturas por cartão de crédito inoperante em produção.

**Veredito: NOT PRODUCTION READY**

---

## 2. Status de Cada Bloqueador Crítico

---

### BUG-C1 — Schema mismatch `internal_webhook_deliveries.subscription_id`

**Status: ⚠️ PARCIALMENTE RESOLVIDO — Regressão de Migration Introduzida**

**O que foi corrigido:**

* ORM `internal_webhook_delivery.py:25` → `nullable=True` ✅
* `tasks.py:_build_payment_internal_delivery` → `subscription_id=None` ✅
* Teste `test_process_webhook_worker_enqueues_internal_delivery_for_standalone_payment` verifica `delivery.subscription_id is None` ✅

**Regressão introduzida (NR-1):**

Existem **duas migrations independentes** fazendo a mesma alteração:

```
20260526_000001_payments_flow.py  upgrade():   ALTER COLUMN subscription_id → nullable=True   ← FIX original  downgrade(): ALTER COLUMN subscription_id → nullable=False20260527_000002_make_subscription_id_nullable.py  ← DUPLICATA  upgrade():   ALTER COLUMN subscription_id → nullable=True   ← redundante  downgrade(): ALTER COLUMN subscription_id → nullable=False  ← PERIGOSO
```

A cadeia de `down_revision` é: `20260527_000002 → 20260527_000001 → 20260526_000002 → 20260526_000001`.

Se um operador rodar `alembic downgrade 20260527_000001`:

1. `20260527_000002.downgrade()` executa → coluna volta a `NOT NULL`
2. O fix de `20260526_000001` ainda está ativo no histórico, mas o schema agora tem `NOT NULL`
3. O código em `tasks.py` ainda insere `subscription_id=None` → **IntegrityError imediato em produção**

Isso é uma **regressão de migration** com downgrade quebrado. Em qualquer hotfix que exija rollback parcial, o sistema voltaria ao estado pre-fix silenciosamente. A FK constraint para `subscriptions.id` também não foi redefinida como `ON DELETE SET NULL`, o que significa que se uma subscription for excluída e houver deliveries vinculadas, ocorrerá `ForeignKeyViolation`.

---

### BUG-C2 — `uq_payments_system_ref` constraint global vs compound

**Status: ✅ RESOLVIDO**

**Evidência:**

* `20260526_000002_payment_system_ref_scope.py`:
  ```
  op.drop_constraint("uq_payments_system_ref", "payments", type_="unique")op.create_unique_constraint("uq_payments_system_ref", "payments", ["system_payment_id", "from_system"])
  ```
* `payment.py:19` → `UniqueConstraint("system_payment_id", "from_system", name="uq_payments_system_ref")` ✅
* Downgrade correto: recria como single-column ✅

**Lacuna de teste:** Não existe teste unitário ou de integração que simule dois sistemas diferentes criando um pagamento com o mesmo `system_payment_id`, validando que o segundo INSERT não falha. A correção é estruturalmente correta, mas sem cobertura de regressão.

---

### BUG-C3 — `PAYMENT_CONFIRMED` e `PAYMENT_DELETED` para assinaturas silently dropped

**Status: ✅ RESOLVIDO — COM DIVERGÊNCIA CRÍTICA COM DOCUMENTAÇÃO ASAAS**

**O que foi corrigido:**

* `process_webhook.py:153-186` → handler para `PAYMENT_CONFIRMED` com subscription ✅
* `process_webhook.py:188-199` → handler para `PAYMENT_DELETED` com subscription ✅
* Ambos salvam `webhook_event`, fazem `uow.commit()`, usam `get_by_provider_id_for_update` ✅
* Testes `test_process_webhook_payment_confirmed_for_subscription` e `test_process_webhook_payment_deleted_for_subscription` ✅

**Divergência Crítica com Documentação Asaas (DIV-NOVA-1):**

A documentação oficial do Asaas ([Payment Events](https://docs.asaas.com/docs/payment-events)) declara explicitamente:

> Cobrança no cartão de crédito recebida no prazo:
> `PAYMENT_CREATED → PAYMENT_CONFIRMED → PAYMENT_RECEIVED (30 dias após PAYMENT_CONFIRMED)`

Isso significa que para cartão de crédito, `PAYMENT_RECEIVED` chega **30 dias** depois de `PAYMENT_CONFIRMED`. O sistema atual ativa a assinatura  **apenas no `PAYMENT_RECEIVED`** . Portanto:

**Um cliente que paga por cartão de crédito ficará com a assinatura em `PENDING` por 30 dias após ter o cartão debitado.**

Isso não é um risco teórico — é o comportamento padrão do Asaas documentado oficialmente. A correção de BUG-C3 registra o pagamento como `CONFIRMED` (correto para auditoria e dados), mas a assinatura continua `PENDING` até o settlement de 30 dias.

O fix correto para cartão de crédito é:  **ativar a assinatura no `PAYMENT_CONFIRMED`** , não no `PAYMENT_RECEIVED`. A recomendação da auditoria original (PR-4 "sem ativar a assinatura ainda") foi insuficiente ao ignorar este prazo.

**Problema adicional com PAYMENT_DELETED (NR-7):**

```
# process_webhook.py:191if payment and payment.payment_status in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:    payment.mark_as_canceled()
```

Se o pagamento estiver em estado `CONFIRMED` (após PAYMENT_CONFIRMED ter sido processado) e um `PAYMENT_DELETED` chegar, o pagamento local permanece `CONFIRMED` mas foi deletado no gateway. Divergência não tratada. O `mark_as_canceled()` em `payment.py:134-138` só aceita `{PENDING, OVERDUE}`.

---

### BUG-C4 — `create_customer` não idempotente localmente

**Status: ⚠️ PARCIALMENTE RESOLVIDO — Race Condition Remanescente**

**O que foi corrigido:**

* `create_customer.py:30-44`:
  ```
  try:    existing = await self.repo.get_by_system_id_and_system(...)    if existing.has_provider_binding():        return existing.provider_customer_id    cus = existingexcept NotFoundError:    cus = Customer(...)
  ```
* `CustomerRepositoryINFRA.get_by_system_id_and_system` implementado corretamente ✅
* Chamadas **sequenciais** agora são idempotentes ✅

**Race condition remanescente:**

Para chamadas **concorrentes** com o mesmo `(system_customer_id, system)`:

1. Worker A: `get_by_system_id_and_system` → `NotFoundError`
2. Worker B: `get_by_system_id_and_system` → `NotFoundError` (antes do commit de A)
3. Worker A: `gateway.create_customer()` → `cus_123`
4. Worker B: `gateway.create_customer()` → `cus_123` (idempotente no gateway ✅)
5. Worker A: `repo.save(cus)` → INSERT bem-sucedido
6. Worker B: `repo.save(cus)` → **`UniqueViolationError` em `uq_customers_system_ref`** → HTTP 500

Não existe `SELECT FOR UPDATE` no `get_by_system_id_and_system`, nem `ON CONFLICT DO NOTHING`, nem retry em `IntegrityError`. Em ambiente multi-worker (que é o caso documentado com ARQ), isso acontece com retry automático do chamador.

**Evidência de teste:** O arquivo `test_create_customer_api.py` usa mock do use case. **Zero testes para a lógica de idempotência do use case em si.**

---

### BUG-C5 — `AsaasAPIError` 5xx não retentado pelos workers

**Status: ✅ RESOLVIDO**

**Evidência:**

* `tasks.py:create_subscription_worker:291-326` → 4xx: terminal + DLQ; 5xx: `raise` para ARQ retry ✅
* `tasks.py:create_payment_worker:421-465` → mesmo padrão ✅
* `tasks.py:cancel_subscription_worker:740-775` → mesmo padrão ✅
* `tasks.py:create_payment_link_worker:535-576` → mesmo padrão ✅

**Lacuna de teste:** `test_cancel_subscription_worker_retries_transient_failures` usa `RuntimeError`, não `AsaasAPIError(status_code=503)`. Não existe teste que valide a discriminação 4xx (terminal) vs 5xx (retentável) especificamente para `AsaasAPIError`. A implementação está correta, mas pode regredir silenciosamente.

---

### BUG-C6 — `Payment.__init__` crash com `net_value=None`

**Status: ✅ RESOLVIDO**

**Evidência:**

* `payment.py:51` → `if net_value is not None and net_value < 0:` ✅
* `mark_as_confirmed` e `mark_as_paid` também protegem `net_value` corretamente ✅

**Lacuna de teste:** Nenhum teste unitário cria `Payment(net_value=None)` explicitamente para validar que não há `TypeError`. O teste mais próximo (`test_process_webhook_acknowledges_overdue_event`) usa `net_value=None` nos details do webhook, mas não instancia `Payment.__init__` com `net_value=None` diretamente.

---

## 3. Evidências no Código — Sumário

| Arquivo                                                               | Linhas-chave                            | Status              |
| --------------------------------------------------------------------- | --------------------------------------- | ------------------- |
| `app/domain/entities/payment.py:51`                                 | `if net_value is not None`            | ✅ BUG-C6           |
| `app/application/use_cases/create_customer.py:30-44`                | Try/except NotFoundError                | ⚠️ BUG-C4 parcial |
| `app/application/use_cases/process_webhook.py:107-108`              | `get_by_provider_id_for_update`       | ✅ RISK-H1          |
| `app/application/use_cases/process_webhook.py:153-186`              | Handler PAYMENT_CONFIRMED sub           | ✅ BUG-C3           |
| `app/application/use_cases/process_webhook.py:188-199`              | Handler PAYMENT_DELETED sub             | ✅ BUG-C3           |
| `app/workers/tasks.py:291-326`                                      | AsaasAPIError 4xx vs 5xx                | ✅ BUG-C5           |
| `app/workers/tasks.py:903-981`                                      | `reconcile_gateway_operations_worker` | ⚠️ Novos riscos   |
| `app/workers/worker.py:119-125`                                     | Cron a cada 15min                       | ✅ RISK-H2          |
| `app/infra/db/models/internal_webhook_delivery.py:25`               | `nullable=True`                       | ✅ BUG-C1 ORM       |
| `app/infra/db/models/payment.py:19`                                 | Compound constraint                     | ✅ BUG-C2 ORM       |
| `app/infra/repo/subscription_repo.py:34-40`                         | `get_by_provider_id_for_update`       | ✅ RISK-H1          |
| `alembic/versions/20260526_000001_payments_flow.py`                 | nullable=True (1ª vez)                 | ✅                  |
| `alembic/versions/20260526_000002_payment_system_ref_scope.py`      | Constraint compound                     | ✅                  |
| `alembic/versions/20260527_000002_make_subscription_id_nullable.py` | nullable=True (DUPLICATA)               | ❌ NR-1             |

---

## 4. Evidências nos Testes

| Teste                                                                             | Cobre                                                     | Qualidade |
| --------------------------------------------------------------------------------- | --------------------------------------------------------- | --------- |
| `test_process_webhook_creates_payment_and_marks_subscription_as_paid`           | PAYMENT_RECEIVED ativa sub                                | ✅        |
| `test_process_webhook_returns_none_for_already_processed_event`                 | Idempotência webhook_event                               | ✅        |
| `test_process_webhook_payment_confirmed_for_subscription`                       | BUG-C3 CONFIRMED                                          | ✅        |
| `test_process_webhook_payment_deleted_for_subscription`                         | BUG-C3 DELETED                                            | ✅        |
| `test_process_webhook_marks_standalone_payment_as_paid`                         | Pagamento avulso RECEIVED                                 | ✅        |
| `test_process_webhook_marks_standalone_payment_as_confirmed`                    | Pagamento avulso CONFIRMED                                | ✅        |
| `test_process_webhook_worker_enqueues_internal_delivery_for_standalone_payment` | BUG-C1 subscription_id=None                               | ✅        |
| `test_cancel_subscription_worker_retries_transient_failures`                    | BUG-C5 parcial (RuntimeError, não AsaasAPIError)         | ⚠️      |
| `test_reconcile_payment_fetches_pending_payment_once_and_persists_change`       | ReconcilePayment                                          | ✅        |
| **AUSENTE**                                                                 | BUG-C4 idempotência create_customer (use case)           | ❌        |
| **AUSENTE**                                                                 | AsaasAPIError 4xx vs 5xx para subscription/payment worker | ❌        |
| **AUSENTE**                                                                 | Payment.**init** com net_value=None                 | ❌        |
| **AUSENTE**                                                                 | SUBSCRIPTION_DELETED lifecycle                            | ❌        |
| **AUSENTE**                                                                 | uq_payments_system_ref multi-sistema                      | ❌        |
| **AUSENTE**                                                                 | reconcile_gateway_operations_worker                       | ❌        |
| **AUSENTE**                                                                 | Migration schema (alembic check)                          | ❌        |
| **AUSENTE**                                                                 | Ativação de assinatura antes de PAYMENT_CONFIRMED (CC)  | ❌        |

---

## 5. Divergências Restantes com a Documentação Oficial do Asaas

| #                    | Divergência                                                                                                                                                                      | Severidade         | Status            |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ | ----------------- |
| **DIV-NOVA-1** | **CC lifecycle: PAYMENT_RECEIVED vem 30 dias após PAYMENT_CONFIRMED. Sistema ativa assinatura no PAYMENT_RECEIVED. Usuários de CC esperam 30 dias com cartão debitado.** | **CRÍTICO** | ❌ Não corrigido |
| DIV-2                | PAYMENT_DELETED não cancela pagamentos em estado CONFIRMED                                                                                                                       | Alto               | ⚠️ Parcial      |
| DIV-3                | `externalReference` não enviado na criação de assinatura no Asaas                                                                                                            | Médio             | ❌ Não corrigido |
| DIV-5                | Sistema limita billing_type a CREDIT_CARD; Asaas suporta BOLETO e PIX para assinaturas                                                                                            | Médio             | ❌ Não corrigido |
| DIV-6                | Asaas retorna status INACTIVE/EXPIRED para assinaturas — não mapeados localmente                                                                                                | Baixo              | ❌ Não corrigido |
| DIV-7                | Fila de webhooks Asaas pausada após 15 respostas não-200 — sem alerta no sistema                                                                                               | Baixo              | ❌ Não corrigido |

---

## 6. Novos Riscos Encontrados

### NR-1 — Migration duplicada com downgrade perigoso

**Severidade: CRÍTICO**

`20260526_000001` e `20260527_000002` fazem a mesma alteração. O `downgrade()` de `20260527_000002` reverte `subscription_id` para `NOT NULL`, quebrando o fix de `20260526_000001`. Qualquer rollback de migration que inclua `20260527_000002` deixa o schema incompatível com o código sem aviso.

### NR-2 — `reconcile_gateway_operations_worker`: `status_response` nunca usado (create_subscription branch)

**Severidade: Alto**

```
status_response = await gateway.verify_status(op.gateway_reference)  # tasks.py:919# ... status_response nunca verificado no branch create_subscription
```

Se a assinatura foi deletada no Asaas, o worker ainda a reconciliaria localmente, criando um registro fantasma com `PENDING`.

### NR-3 — `reconcile_gateway_operations_worker`: deserialização de `value` do JSONB

**Severidade: Alto**

```
value=op.request_payload["value"],  # tasks.py:938
```

`request_payload` é JSONB — `Decimal` é serializado como float/string no JSON. `Subscription.__init__` espera `Decimal`. A conversão não é feita, causando `TypeError` ou imprecisão aritmética em operações financeiras.

### NR-4 — `reconcile_gateway_operations_worker`: sessão SQLAlchemy reutilizada após rollback

**Severidade: Alto**

O loop itera sobre todas as operações REQUIRES_RECONCILIATION em uma única `AsyncSession`. Se uma operação falha e `uow.rollback()` é chamado, a sessão continua sendo usada para as operações seguintes. Em SQLAlchemy async, após um rollback a sessão inicia uma nova transação implícita, mas objetos já carregados ficam em estado `detached` ou com dados stale. Pode causar escritas silenciosamente inconsistentes.

### NR-5 — `reconcile_gateway_operations_worker`: sem lock de concorrência para cron multi-worker

**Severidade: Alto**

O cron é configurado a cada 15 min. Se um worker demora mais de 15 min processando um lote grande, dois workers processarão as mesmas `GatewayOperation` simultaneamente, potencialmente criando:

* Assinaturas duplicadas localmente
* Pagamentos duplicados
* Operação marcada como COMPLETED duas vezes

Não há `SELECT FOR UPDATE` nem Redis lock nas operações do reconcile worker.

### NR-6 — `PAYMENT_CONFIRMED` para subscription: lock desnecessário

**Severidade: Baixo**

```
sub = await self.sub_repo.get_by_provider_id_for_update(...)  # process_webhook.py:154# ... sub nunca é modificado no branch PAYMENT_CONFIRMED
```

`SELECT FOR UPDATE` adquirido, transação committed sem modificar a subscription. Lock desnecessário que serializa processamento concorrente de webhooks distintos na mesma assinatura.

### NR-7 — `PAYMENT_DELETED` não trata pagamentos em estado `CONFIRMED`

**Severidade: Médio**

```
if payment and payment.payment_status in {PaymentStatus.PENDING, PaymentStatus.OVERDUE}:    payment.mark_as_canceled()
```

Se o fluxo foi CC: `PAYMENT_CONFIRMED` → local é `CONFIRMED` → `PAYMENT_DELETED` chega → local permanece `CONFIRMED`. Divergência entre gateway (deleted) e local (confirmed).

### NR-8 — BUG-C4 race condition concorrente não resolvido

**Severidade: Médio**

Detalhado na seção BUG-C4. Dois workers simultâneos com mesmo `(system_customer_id, system)` causam `UniqueViolationError` → HTTP 500.

### NR-9 — Ausência de testes para AsaasAPIError 4xx vs 5xx nos workers de subscription/payment

**Severidade: Médio**

O comportamento correto existe no código mas pode regredir sem cobertura de teste. O teste existente usa `RuntimeError`, não `AsaasAPIError`.

---

## 7. Checklist de Produção

| #  | Item                                                                        | Status |
| -- | --------------------------------------------------------------------------- | ------ |
| 1  | BUG-C1 ORM e código corrigidos                                             | ✅     |
| 2  | BUG-C1 migration sem duplicatas/downgrade seguro                            | ❌     |
| 3  | BUG-C2 constraint compound corrigida e em production DB                     | ✅     |
| 4  | BUG-C3 PAYMENT_CONFIRMED handler implementado                               | ✅     |
| 5  | BUG-C3 PAYMENT_CONFIRMED ativa assinatura CC (não espera 30 dias RECEIVED) | ❌     |
| 6  | BUG-C3 PAYMENT_DELETED trata estado CONFIRMED                               | ❌     |
| 7  | BUG-C4 idempotência create_customer (sequencial)                           | ✅     |
| 8  | BUG-C4 idempotência create_customer (concorrente)                          | ❌     |
| 9  | BUG-C5 AsaasAPIError 4xx terminal / 5xx retentável                         | ✅     |
| 10 | BUG-C6 net_value=None protegido                                             | ✅     |
| 11 | RISK-H1 SELECT FOR UPDATE em PAYMENT_RECEIVED                               | ✅     |
| 12 | RISK-H2 Worker de reconciliação criado                                    | ✅     |
| 13 | reconcile_gateway_operations_worker corretamente implementado               | ❌     |
| 14 | Migration chain sem divergências de downgrade                              | ❌     |
| 15 | Suite de testes cobre cenários críticos de billing (CC lifecycle)         | ❌     |
| 16 | Testes de AsaasAPIError 4xx vs 5xx nos workers                              | ❌     |
| 17 | externalReference enviado ao Asaas nas assinaturas                          | ❌     |
| 18 | Sistema suporta BOLETO e PIX para assinaturas                               | ❌     |
| 19 | Sem worker de reconciliação com race condition multi-worker               | ❌     |
| 20 | Downgrade de migration é seguro e testado em staging                       | ❌     |

---

## 8. Veredito Final

```
╔══════════════════════════════════════════════════════════════════════════╗║                                                                          ║║   ❌  NOT PRODUCTION READY                                               ║║                                                                          ║╚══════════════════════════════════════════════════════════════════════════╝
```

### Justificativa

**4 bugs foram corrigidos** (C2, C5, C6, e C3 parcialmente). A expansão de cobertura de testes foi positiva. O padrão SELECT FOR UPDATE foi implementado corretamente. O worker de reconciliação periódica foi criado.

No entanto, existem  **3 bloqueadores que impedem produção** :

---

**Bloqueador 1 — DIV-NOVA-1: Ciclo de vida CC não está correto (30 dias sem acesso)**

A documentação oficial do Asaas confirma que `PAYMENT_RECEIVED` para cartão de crédito ocorre  **30 dias após `PAYMENT_CONFIRMED`** . O sistema ativa assinaturas somente no `PAYMENT_RECEIVED`. Um cliente que paga por cartão ficará com a assinatura em `PENDING` por 30 dias — completamente inaceitável em produção. A recomendação da auditoria original (PR-4 "sem ativar a assinatura ainda") foi insuficiente ao não considerar esse prazo. A ativação no `PAYMENT_CONFIRMED` é necessária para CC.

**Bloqueador 2 — NR-1: Migration chain quebrado**

`20260527_000002` é uma migration duplicada cujo `downgrade()` reverte o fix de BUG-C1. Executar `alembic downgrade 20260527_000001` em qualquer deploy com problema restaura o `NOT NULL` constraint — quebrando silenciosamente toda a entrega de internal webhooks para pagamentos avulsos, que era exatamente o bug que foi corrigido. Em produção real, hotfixes frequentemente exigem rollback parcial de migrations.

**Bloqueador 3 — NR-5: `reconcile_gateway_operations_worker` quebrado em multi-worker**

O worker de reconciliação (novo, implementado para RISK-H2) não tem lock de concorrência. Com o cron a cada 15 min e múltiplos workers ARQ, dois processos podem reconciliar as mesmas `GatewayOperation` simultaneamente, criando assinaturas e pagamentos duplicados. Além disso, `status_response` não é verificado (NR-2) e `value` do JSONB não é convertido para `Decimal` (NR-3). O worker foi criado para resolver um risco mas introduziu três novos.

---

 **Para atingir Production Ready With Minor Risks** , são necessários:

1. Remover `20260527_000002` ou corrigir seu `downgrade()` para não reverter `nullable=True`
2. Implementar ativação de assinatura no `PAYMENT_CONFIRMED` para cartão de crédito
3. Adicionar Redis lock no `reconcile_gateway_operations_worker` (ex: `SET NX reconcile_lock TTL=14min`)
4. Usar `SELECT FOR UPDATE` por operação no reconcile worker ou processar em sessões independentes
5. Verificar `status_response` antes de reconciliar subscription
6. Converter `op.request_payload["value"]` para `Decimal` no reconcile worker
7. Adicionar `ON CONFLICT DO NOTHING` ou retry em `create_customer` para concorrência
8. Testes para `AsaasAPIError` 4xx/5xx nos workers de subscription e payment

---

*Auditoria de validação gerada por Claude Sonnet 4.6 em 2026-05-27. Baseada em leitura completa de todas as migrations, use cases, workers, repositórios, testes e documentação oficial do Asaas: [Payment Events](https://docs.asaas.com/docs/payment-events) · [Subscription Events](https://docs.asaas.com/docs/subscription-events) · [Checkout Recorrente](https://docs.asaas.com/docs/checkout-with-subscription-recurring) · [Fluxos de Webhook](https://docs.asaas.com/docs/fluxos-de-webhook)*

---

**Resumo dos 3 bloqueadores que impedem deploy:**

1. Assinatura CC fica PENDING por 30 dias após pagamento confirmado (divergência Asaas documentada)
2. Migration `20260527_000002` tem downgrade que reverte o fix do BUG-C1
3. `reconcile_gateway_operations_worker` cria duplicatas em ambiente multi-worker
