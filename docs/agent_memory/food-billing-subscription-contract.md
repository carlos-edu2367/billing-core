# Contrato Neectify Food ↔ Billing Core (assinaturas)

Descoberto em 2026-07-29 por leitura + harness executável (`docs/reports/harness_food_billing_contract.py`).
Relatório completo: `docs/reports/2026-07-29-integracao-food-billing-assinaturas.md`.

## Formato do contrato (verificado)

- Food → Billing: `POST /v1/customers` (201, devolve `provider_customer_id`), `POST /v1/subscriptions` (202, devolve `job_id`), `POST /v1/subscriptions/{id}/cancel` (202), `GET /v1/jobs/{id}`.
- Headers internos: `X-System: neectify_food`, `X-API-Key`, `Idempotency-Key`.
- Billing → Food: `POST {webhook_link}` com `SendInternalWebhookSubscription`
  = `{event, subscription_id, system_sub_id, subscription_expires_at, payment_date}`.
  Headers enviados: `X-Webhook-Signature-256`, **`X-Webhook-Id`**, `X-Webhook-Event`.
  O Food usa `X-Webhook-Id` como chave de idempotência (`build_billing_event_key`);
  ele **não** envia `X-Request-ID`, apesar de a rota ainda aceitar esse header.
- `InternalEventType` tem 6 valores. `PAYMENT_STATUS_UPDATED` é o único sem ramo por
  nome no Food — ele é resolvido pelos campos de status do payload.

## Assinatura HMAC — assimetria intencional

O Billing Core assina `json.dumps(payload, sort_keys=True, separators=(",",":"))` mas
transmite via `httpx json=`, que serializa diferente. O Food re-normaliza o corpo
recebido com os mesmos parâmetros antes de verificar, então **bate** — inclusive com
não-ASCII. É correto mas frágil: trocar o serializador de qualquer lado invalida
todos os webhooks de uma vez. Não "consertar" um lado só.

## Emissão de webhook interno — três pontos, sem caminho genérico

Emissores de entrega de assinatura: `process_webhook`, `cancel_subscription_worker`
e o ramo `cancel_subscription` do reconciliador. Todos passam por
`_persist_internal_delivery()` (build → checa `dedupe_key` → salva → commit → devolve
o id a enfileirar).

O `dedupe_key` de um cancelamento é `SUBSCRIPTION_INACTIVATED:{sub_id}:no-payment`
nos três caminhos — de propósito: se o Asaas mandar `SUBSCRIPTION_DELETED` depois de
o worker já ter notificado, a entrega é deduplicada em vez de duplicada.

**Ao adicionar qualquer evento de ciclo de vida, o emissor precisa ser adicionado
explicitamente — não há despacho automático.** E todo `InternalEventType` novo
precisa de um ramo em `HandleBillingWebhookUseCase` do Food, senão vira no-op
silencioso; `test_S4c_every_internal_event_has_a_consumer_branch` no harness
falha se isso acontecer.

## Armadilhas ao mexer nesse fluxo

- `system_sub_id` que o Food emite é `f"{store_id}:{plano}:{attempt_ref}"`, **não** um
  UUID. O `attempt_ref` é regenerado a cada tentativa, então a deduplicação do Billing
  Core por `get_by_system_ref` nunca dispara para o Food. Todo código que faz
  `UUID(system_sub_id)` está morto — o fallback de resolução do webhook no Food ainda
  sofre disso (defeito A1, em aberto).
- O Food só espera o job por 3,5 s (`delays=(0.5,1.0,2.0,3.0)`). A retentativa
  reconsulta `billing_job_id` em vez de criar outra assinatura — **não remova essa
  guarda**, é o que impede cobrança dobrada no Asaas.
- `_RENEWAL_GRACE = timedelta(days=3)` em `Food/src/domain/subscription/entity.py` é
  a margem entre `expires_at` e o bloqueio efetivo. É decisão comercial, não técnica.
- `process_webhook` trata assinatura e pagamento avulso em ramos diferentes,
  discriminados por `payload.details.subscription` estar preenchido. Conferir em qual
  ramo o evento cai antes de mudar qualquer coisa ali.
- A suíte do Billing Core (153 testes) e a do Food (48 em subscription/plan) passam
  verdes sem cobrir nenhum defeito de contrato entre os dois. Vários fakes de teste
  modelam tipos que divergem do ORM real.

## Configuração acoplada

`BACKEND_URL` do Food (default `http://localhost:8000`) vira o `webhook_link`. O Billing
Core exige HTTPS e host em `ALLOWED_INTERNAL_WEBHOOK_HOSTS`, senão 422 em toda criação
de assinatura. Não há validação no boot de nenhum dos lados.
