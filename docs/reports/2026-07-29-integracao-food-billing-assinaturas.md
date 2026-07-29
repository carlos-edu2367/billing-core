# Validação da integração Neectify Food ↔ Billing Core — Assinaturas

**Data:** 2026-07-29
**Escopo:** ciclo de vida completo de assinatura entre `Neectify Food/backend` (consumidor) e `billing` (produtor), gateway Asaas.
**Método:** leitura de código + harness executável que carrega **os dois codebases no mesmo processo** e força cada falha (`docs/reports/harness_food_billing_contract.py`, 18 probes, todas passando — cada probe *afirma o comportamento observado*, inclusive quando ele é o defeito).

**Estado das suítes existentes:** Billing Core `153 passed`; Food `tests/subscription tests/plan` `48 passed`. Nenhuma cobre os defeitos abaixo.

---

> **Atualização de 2026-07-29 (mesma data, após a validação):** os cinco críticos
> foram corrigidos. As seções C1–C5 abaixo descrevem o defeito **como encontrado**;
> a resolução de cada um está em [Correções aplicadas](#correções-aplicadas) no fim
> do documento. A1 e M1–M4 seguem em aberto.

## Sumário

| # | Severidade | Defeito | Efeito financeiro | Status |
|---|---|---|---|---|
| C1 | Crítico | Renovação mensal descartada pela idempotência do Food | Renovação some; bloqueia todo mundo se C4 for corrigido isolado | Corrigido |
| C2 | Crítico | Cancelamento nunca notifica o Food pelo caminho próprio | Loja cancela e mantém plano pago para sempre | Corrigido |
| C3 | Crítico | Inadimplência / estorno / chargeback descartados no Billing Core | Loja para de pagar e segue ATIVA | Corrigido |
| C4 | Crítico | Vencimento nunca bloqueia no Food | Assinatura vencida = acesso total | Corrigido |
| C5 | Crítico | Job lento duplica assinatura no Asaas | Cobrança dobrada | Corrigido |
| A1 | Alto | Webhook perdido se chegar antes do Food gravar o id | Loja paga e não é liberada | **Em aberto** |
| M1–M4 | Médio | Ramo morto, client duplicado, doc incompleta, acoplamento de config | — | M1 corrigido; M2–M4 em aberto |

O caminho feliz (criar → pagar → ativar) **funciona**. O que não funciona é tudo depois do primeiro pagamento — que é exatamente onde o dinheiro recorrente está.

---

## C1 — A renovação mensal é descartada pela idempotência do próprio Food

**Onde:** `Neectify Food/backend/src/presentation/api/v1/subscription.py:257-276` e `billing/app/infra/interfaces/internal_webhook.py:48`

O Billing Core identifica cada entrega com um header único:

```python
# billing/app/infra/interfaces/internal_webhook.py:48
headers["X-Webhook-Id"] = webhook_id          # UUID da InternalWebhookDelivery
```

O Food lê **outro header**, que o Billing Core nunca envia:

```python
# Food subscription.py:214
x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
```

O payload `SendInternalWebhookSubscription` também não tem `event_id` nem `job_id`. Restam apenas os fallbacks:

```python
event_key = payload.event_id or x_request_id or payload.job_id or f"{payload.event}:{billing_id}"
#              None              None            None          → "PAYMENT_RECEIVED:<subscription_id>"
```

Essa chave é **constante para toda a vida da assinatura**. Na renovação do mês 2, `reserve()` encontra a linha do mês 1; como `subscription_expires_at` e `payment_date` mudaram, o hash diverge e ele levanta `IdempotencyConflictError`, que a rota trata como duplicata:

```python
except IdempotencyConflictError:
    return {"received": True, "duplicate": True}   # evento descartado sem processar
```

E não há escapatória por TTL: `reserve()` filtra apenas por `(tenant_id, scope, key)` — nunca lê `expires_at` — e o único `DELETE` de `IdempotencyKeyModel` no projeto é a exclusão LGPD da loja (`src/application/store/use_cases.py:226`). Não existe job de limpeza.

**Probes:** `test_S9_monthly_renewals_collapse_to_one_idempotency_key`, `test_S9b_renewal_is_dropped_by_reserve`

**Impacto hoje:** mascarado por C4 — como nada verifica `expires_at`, a loja continua ativa mesmo sem o evento. **Impacto se C4 for corrigido sozinho:** toda loja pagante é bloqueada ao fim do primeiro ciclo. Corrija C1 **antes** de C4.

**Correção:** ler `X-Webhook-Id` no Food (uma linha), e/ou incluir `payment_date` na chave. O `dedupe_key` do Billing Core já é único por pagamento (`tasks.py:78`).

---

## C2 — O cancelamento nunca notifica o Food pelo caminho próprio

**Onde:** `billing/app/workers/tasks.py` — `cancel_subscription_worker` (linhas 457-577)

O Food solicita o cancelamento e, deliberadamente, **não** marca localmente como cancelado — espera o webhook autoritativo:

```python
# Food use_cases.py:236-240
"""Cancelamento no Billing Core é assíncrono: (...) O status local vira
``cancelled`` apenas quando o webhook ``SUBSCRIPTION_INACTIVATED`` chega."""
```

Mas o único emissor de webhook interno de assinatura no Billing Core é `process_webhook`:

```
$ grep -rn "_build_internal_delivery" app/ | grep -v "def _build"
app/workers/tasks.py:178:    delivery = await _build_internal_delivery(result, sub_repo, payment_repo)
```

`cancel_subscription_worker` cancela no Asaas, cancela localmente, marca a operação como `COMPLETED` — e **não enfileira entrega nenhuma**. O ramo `cancel_subscription` do reconciliador (`tasks.py:793-803`) também não.

O Food só é avisado se o **Asaas** devolver `SUBSCRIPTION_DELETED`/`SUBSCRIPTION_INACTIVATED`. E o `docs/INTEGRATION.md` (linhas 44-52) manda configurar no Asaas apenas os quatro eventos `CHECKOUT_*` — nenhum evento de assinatura ou de pagamento. Seguindo o go-live ao pé da letra, esse retorno não existe.

**Probes:** `test_S5b_cancel_path_never_builds_a_delivery` (prova que o caminho não emite), `test_S5_inactivated_event_downgrades_store_when_it_arrives` (prova que o Food trata corretamente *quando* chega).

**Efeito:** lojista cancela → Billing Core cancela no Asaas → cobrança para → **o Food nunca faz o downgrade e a loja mantém o plano pago indefinidamente, de graça.**

**Correção:** emitir a entrega interna no próprio `cancel_subscription_worker` após `mark_completed` (o `ProcessWebhookResponse` com `SUBSCRIPTION_INACTIVATED` já existe e o Food já sabe consumi-lo), em vez de depender do round-trip pelo Asaas.

---

## C3 — Inadimplência, estorno e chargeback são descartados no Billing Core

**Onde:** `billing/app/application/use_cases/process_webhook.py:91-104`

```python
if payload.event in (EventType.UNKNOWN, EventType.PAYMENT_OVERDUE,
                     EventType.PAYMENT_CHARGEBACK_REQUESTED, EventType.PAYMENT_REFUNDED):
    logger.warning("Webhook recebido sem ação implementada", ...)
    event.mark_as_processed()
    return None          # nenhuma entrega interna
```

O evento é marcado como processado e nada é propagado. A assinatura no Billing Core permanece `ACTIVE`.

A ironia é que **o Food já sabe tratar esses casos** — `HandleBillingWebhookUseCase` tem ramos para `PAYMENT_OVERDUE` e `PAYMENT_REFUNDED` chamando `mark_overdue()`. São ramos mortos: nada os alcança.

**Probes:** `test_S4_lifecycle_events_produce_no_delivery` (parametrizado nos 3 eventos, todos retornam `None` e a assinatura segue `ACTIVE`), `test_S4b_food_has_dead_handlers_for_those_events`.

**Efeito:** cartão expira ou é recusado → o Asaas avisa → o Billing Core loga um warning e joga fora → a loja usa o plano pago sem pagar, sem limite de tempo. `apply_gateway_payment_status` já implementa a transição correta e é usada para pagamentos avulsos.

---

## C4 — Vencimento não bloqueia nada no Food

**Onde:** `Neectify Food/backend/src/domain/subscription/entity.py:33-52`

```python
@property
def is_active(self) -> bool:
    return self.status == SubscriptionStatus.active or self.is_trial_active

@property
def is_blocked(self) -> bool:
    return self.status in (cancelled, expired) or self.is_trial_expired
```

Nenhuma das duas olha `expires_at` para assinatura paga — só para trial. Uma vez `active`, sempre `active`. E não existe cron que expire assinatura: `WorkerSettings.cron_jobs` (`src/infrastructure/workers/main.py:253-257`) roda apenas `process_outbox` e `reconcile_payments` — este último reconcilia pagamentos de pedido no Mercado Pago, não assinaturas.

**Probe:** `test_S7_expired_paid_subscription_is_never_blocked` — assinatura `active` com `expires_at` 400 dias no passado retorna `is_active=True`, `is_blocked=False`.

**Correção:** considerar `expires_at` em `is_active`/`is_blocked` — **mas só depois de C1**, senão bloqueia clientes adimplentes.

---

## C5 — Job lento duplica a assinatura no Asaas (cobrança dobrada)

**Onde:** `Food/src/infrastructure/integrations/billing_core.py:152-179` + `src/application/subscription/use_cases.py:52-66`

O Food espera o job por no máximo **3,5 s** (`delays=(0.5, 1.0, 2.0, 3.0)`, sem sleep após a última tentativa). Nesse intervalo o worker precisa criar a assinatura no Asaas **e** buscar os pagamentos dela — duas chamadas HTTP externas. Estourando, `wait_for_job_result` devolve `None` e o Food grava `billing_core_sub_id=None, checkout_url=None`.

O lojista fica sem link e clica "assinar" de novo. O branch de retomada exige `existing.checkout_url` preenchido:

```python
if (existing and existing.billing_core_sub_id and existing.plan == data.plan
        and existing.status in (pending, overdue) and existing.checkout_url):
    return SubscriptionOutput.model_validate(existing)     # não entra: os dois são None
```

Cai fora, e como o plano é o mesmo, `is_plan_change` é `False` — a primeira assinatura **não é cancelada**. Aí gera um `attempt_ref` novo:

```python
attempt_ref = uuid4().hex[:12]
system_sub_id = f"{store_id}:{data.plan.value}:{attempt_ref}"
```

`system_sub_id` e `Idempotency-Key` diferentes ⇒ a deduplicação do Billing Core (`get_by_system_ref`, `create_subscription.py:58`) não dispara. Nasce uma **segunda assinatura recorrente no Asaas**, e a primeira continua ativa e cobrando.

**Probe:** `test_S6_slow_job_creates_a_second_asaas_subscription` — duas chamadas a `create_subscription`, dois `system_sub_id` distintos, zero cancelamentos.

Agrava: nada no Food volta a consultar o job depois. `get_job_status` existe no client mas não tem chamador fora de `wait_for_job_result`; não há cron de reconciliação de assinatura. Uma assinatura criada com sucesso no Billing Core mas não confirmada a tempo fica órfã para sempre no Food.

---

## A1 — Webhook perdido se chegar antes do Food gravar `billing_core_sub_id`

**Onde:** `Food/src/application/subscription/use_cases.py:188-201`

A resolução tem dois caminhos, e o segundo é morto:

```python
try:
    store_id = UUID(payload.system_sub_id)
except ValueError:
    return                      # descarta em silêncio
```

O `system_sub_id` que o **próprio Food** emite é `f"{store_id}:{plan}:{attempt_ref}"` — nunca um UUID puro. O Billing Core devolve essa string verbatim. Logo, `UUID()` sempre levanta e o fallback nunca resolve nada.

**Probes:** `test_S3_unknown_subscription_id_is_silently_dropped` (formato real do Food → evento perdido, loja paga e não é liberada) e `test_S3b_fallback_works_only_if_system_sub_id_is_bare_uuid` (mesmo caso com UUID puro → ativa normalmente; prova que a intenção do código era essa).

**Correção:** `payload.system_sub_id.split(":")[0]` antes do `UUID()`, ou parar de embutir metadados no `system_sub_id`.

---

## Médios

**M1 — `PAYMENT_STATUS_UPDATED` com `subscription_id` é no-op silencioso no Food.** O `InternalEventType` do Billing Core tem quatro valores; o Food trata três. `process_webhook.py:222` emite `PAYMENT_STATUS_UPDATED` quando o pagamento da assinatura não é cartão. Hoje é inalcançável (assinatura é sempre `CREDIT_CARD`, hardcoded em `create_subscription.py:96`), mas vira bug silencioso no dia em que boleto/pix for suportado.

**M2 — Client duplicado morto.** `Food/src/infrastructure/billing/client.py` define uma segunda `BillingCoreClient` sem nenhum importador (o código todo usa `infrastructure/integrations/billing_core.py`). Ela usa `raise_for_status()` e um contrato de resposta diferente. Remover antes que alguém a use por engano.

**M3 — `docs/INTEGRATION.md` não documenta o contrato de assinatura.** O DTO do Food afirma "(see Billing Core INTEGRATION.md)" para `{event, subscription_id, system_sub_id, subscription_expires_at, payment_date}`, mas o documento cobre só o fluxo de checkout. A tabela de eventos a configurar no Asaas lista apenas `CHECKOUT_*` — omissão que **causa C2** em produção.

**M4 — Acoplamento de configuração não validado no boot.** `webhook_link` é montado de `BACKEND_URL` (default `http://localhost:8000`) e o Billing Core exige HTTPS + host em `ALLOWED_INTERNAL_WEBHOOK_HOSTS`. Fora disso, **toda** criação de assinatura falha com 422 → o Food converte em `ValueError` → 422 genérico ao lojista. Falha tarde e com mensagem ruim.
**Probe:** `test_S8_webhook_link_scheme_and_host` (https ok; http e localhost rejeitados).

---

## O que foi validado e está correto

- **Assinatura HMAC sobrevive ao round-trip**, inclusive com não-ASCII. O Billing Core assina `json.dumps(sort_keys=True, separators=(",",":"))` mas transmite a serialização do httpx; o Food re-normaliza o corpo recebido com os mesmos parâmetros antes de verificar. Bate — mas é frágil por construção: qualquer mudança de serializador em qualquer um dos lados quebra a verificação de todos os webhooks de uma vez. **Probes:** `test_S1_signature_roundtrip_holds`, `test_S1b_signature_roundtrip_with_non_ascii`.
- **`provider_customer_id`** — o Billing Core retorna 201 com essa chave e o client do Food a aceita entre os aliases.
- **`neectify_food`** existe em `System` e casa com `BILLING_CORE_SYSTEM`; header `X-System` e campo `system` são coerentes.
- **Ativação por `PAYMENT_RECEIVED`** funciona ponta a ponta, incluindo `update_plan` da loja (`test_S2`).
- **`SUBSCRIPTION_INACTIVATED`**, quando chega, cancela e faz downgrade para `starter` corretamente (`test_S5`).

---

## Correções aplicadas

Todas via TDD (teste vermelho antes da implementação). Suítes finais: Billing Core
**161 passed** (era 153), Food **705 passed, 1 failed** — a falha é
`tests/security/test_final_hardening.py::test_admin_identity_can_access_and_is_audited`,
**pré-existente e não relacionada** (reproduz com `src/` revertido via `git stash`).

### C1 — chave de idempotência por entrega

`build_billing_event_key()` extraída da rota em
`Food/src/presentation/api/v1/subscription.py` e o header `X-Webhook-Id` passou a
ser lido. O fallback agora inclui a data do ciclo (`payment_date`, ou
`subscription_expires_at`), então dois meses nunca colidem mesmo sem header —
defesa em profundidade. Reentrega do mesmo evento continua deduplicando.
Testes: `Food/tests/subscription/test_webhook_idempotency.py` (5).

### C2 — cancelamento emite o evento por conta própria

Novo helper `_persist_internal_delivery()` em `billing/app/workers/tasks.py`.
`cancel_subscription_worker` passou a construir e enfileirar a entrega
`SUBSCRIPTION_INACTIVATED` quando o resultado é `CANCELED` — e o ramo
`cancel_subscription` do reconciliador também. O `dedupe_key` é idêntico ao que o
round-trip pelo Asaas produziria, então um eventual `SUBSCRIPTION_DELETED` do
gateway **não** gera entrega duplicada. O cancelamento deixou de depender de
configuração de webhook de assinatura no Asaas.
Testes: `tests/test_cancel_subscription_worker.py` (2), `tests/test_reconcile_worker.py` (1).

### C3 — ciclo de vida espelhado para o consumidor

`ProcessWebhookService._process_subscription_lifecycle_webhook()` trata
`PAYMENT_OVERDUE`, `PAYMENT_REFUNDED` e `PAYMENT_CHARGEBACK_REQUESTED` quando o
evento tem `details.subscription`. `InternalEventType` ganhou `PAYMENT_OVERDUE` e
`PAYMENT_CHARGEBACK_REQUESTED`. `UNKNOWN` continua descartado.

Duas guardas incluídas: um OVERDUE que chega fora de ordem depois da confirmação é
ignorado (`_OVERDUE_REJECTED_FROM`) em vez de derrubar o job com `DomainError`, e
um evento que não muda o estado local não gera notificação repetida.
Testes: `tests/test_process_webhook_use_case.py` (5).

### C4 — vencimento bloqueia

Nova propriedade `is_expired` em `Food/src/domain/subscription/entity.py`,
consumida por `is_active` e `is_blocked` — o gate de plano em
`presentation/dependencies/plan.py` passa a devolver 402 para assinatura vencida.

**Decisão de produto embutida:** `_RENEWAL_GRACE = timedelta(days=3)`. Sem margem,
qualquer atraso do webhook de renovação bloquearia lojista adimplente; com ela, uma
loja que realmente parou de pagar mantém acesso por até 3 dias. O valor está isolado
numa constante nomeada — **ajuste conforme a política comercial.**
Testes: `Food/tests/subscription/test_entity.py` (7).

### C5 — retentativa reconsulta o job em vez de recriar

`CreateSubscriptionUseCase.execute` ganhou um ramo antes da criação: se existe
assinatura do mesmo plano em `pending`/`overdue`, com `billing_job_id` e sem
`checkout_url`, o job é reconsultado. Se completou, o `subscription_id` e o
`checkout_url` são recuperados; se ainda processa, retorna como está. Só quando o
job falhou terminalmente (`BillingCoreError`) é que uma nova assinatura é criada —
nesse caso nada foi criado no gateway, então recriar é seguro.

Isso também resolve a assinatura órfã: o lojista recupera o link de pagamento na
própria retentativa, sem intervenção.
Testes: `Food/tests/subscription/test_use_cases.py` (3).

### M1 — `PAYMENT_CHARGEBACK_REQUESTED` tem ramo no Food

`HandleBillingWebhookUseCase` trata chargeback junto com estorno (`mark_overdue`).
O harness ganhou `test_S4c_every_internal_event_has_a_consumer_branch`, que falha se
alguém adicionar um `InternalEventType` sem tratamento no consumidor.

### O que continua em aberto

**A1** (fallback por `system_sub_id` morto — `UUID()` sobre string composta),
**M2** (client duplicado em `Food/src/infrastructure/billing/client.py`),
**M3** (`INTEGRATION.md` sem o contrato de assinatura) e **M4** (`BACKEND_URL`
sem validação no boot). A probe `test_S3_unknown_subscription_id_is_silently_dropped`
segue afirmando o comportamento quebrado de A1, de propósito.

## Reproduzir

```bash
cd "C:/Users/reali/Documents/Neectify/billing" && python -m pytest docs/reports/harness_food_billing_contract.py -q
```

O harness tem os caminhos dos dois repositórios embutidos no topo e fica fora de `testpaths` (`pytest.ini` aponta só para `tests`), então não entra na suíte de CI.

Depois das correções ele deixou de documentar defeitos e virou **suíte de regressão do contrato** (23 probes). As probes que antes afirmavam o comportamento quebrado foram invertidas, e as que passavam por artefato do próprio fake — chave de idempotência copiada em vez de importada, repositório de pagamento sempre vazio, `billing_job_id` ausente — foram corrigidas para usar as funções de produção reais. Essa é a mesma armadilha descrita em "Limites desta validação": fake que diverge do real mascara o defeito.

## Limites desta validação

Nada foi executado contra o Asaas (nem sandbox), contra Redis/ARQ reais ou contra Postgres. Todos os defeitos são de contrato entre os dois serviços, confirmados por execução das classes de produção com repositórios e gateway falsos. **Não verificado:** se o Asaas de fato emite `SUBSCRIPTION_DELETED` ao receber `DELETE /subscriptions/{id}` e se a conta atual está inscrita nesses eventos — isso decide se C2 é "cancelamento não funciona" ou "cancelamento depende de config não documentada". Vale confirmar no painel do Asaas antes de escolher a correção.
