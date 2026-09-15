# Assinatura: back_url por requisicao e consulta de status — Design Spec

**Contexto:** o Mercado Pago virou o gateway padrao (`docs/superpowers/plans/2026-09-14-mercadopago-gateway-padrao.md`, ja em producao). Ao integrar o checkout de assinaturas do Marketfy sobre essa base, duas lacunas do contrato interno de `POST /v1/subscriptions` bloqueiam a experiencia do consumidor:

1. O `back_url` do `preapproval` do Mercado Pago vem so de `MERCADOPAGO_SUBSCRIPTION_BACK_URL`, uma variavel global compartilhada por todos os sistemas internos (`marketfy`, `neectify_shop`, `neectify_food`). O consumidor nao consegue levar o pagador de volta para uma pagina propria com o id da assinatura local, so para uma URL fixa.
2. Nao existe um jeito de o consumidor perguntar "qual o status agora?" de uma assinatura sem esperar um webhook. Isso importa quando o pagador volta do Mercado Pago logo apos autorizar o cartao: o `preapproval` ja esta `authorized`, mas a primeira fatura (e o webhook `PAYMENT_RECEIVED`) só chega ~1h depois.

## Decisao

Duas mudancas aditivas, sem alterar nenhum contrato existente:

### 1. `back_url` opcional por requisicao

`POST /v1/subscriptions` aceita um campo opcional `back_url`. Quando presente, o adapter do Mercado Pago usa esse valor no `preapproval.back_url` em vez de `settings.MERCADOPAGO_SUBSCRIPTION_BACK_URL`. Quando ausente, comportamento identico ao atual (usa a variavel global). O Asaas ignora o campo (nao tem back_url por assinatura).

Validado com a mesma regra de host permitido do checkout avulso (`settings.effective_checkout_redirect_hosts`, ja usada em `CreatePaymentRequest`), para nao abrir redirect arbitrario.

### 2. `GET /v1/subscriptions/{subscription_id}`

Endpoint novo, somente leitura, com o mesmo padrao de autenticacao/autorizacao de `GET /v1/jobs/{job_id}`: exige `X-System`/`X-API-Key` com escopo `subscriptions:read`, e so responde para o sistema dono da assinatura (`subscription.belongs_to_system(auth.system)`, mesma checagem que `POST /v1/subscriptions/{id}/cancel` ja faz).

Devolve o status atual da assinatura consultado ao vivo no gateway (`gateway.verify_status()`, metodo que ja existe nos dois adapters — nenhuma mudanca de adapter necessaria):

```json
{
  "subscription_id": "018f...-local-id",
  "status": "ACTIVE",
  "next_due_date": "2026-10-15",
  "value": "129.90",
  "cycle": "MONTHLY"
}
```

`status` usa o vocabulario ja compartilhado pelos dois adapters (`ACTIVE`, `PENDING`, `PAUSED`, `CANCELED`), o mesmo que `SubscriptionStatusResponse.status` ja produz.

## Por que nao um evento de webhook novo

Emitir um evento interno no momento em que o Mercado Pago autoriza o cartao (`subscription_preapproval` com `status=authorized`) exigiria: (a) o adapter passar a tratar um status que hoje ele ignora deliberadamente (`preapproval_notification_to_webhook` só reage a `cancelled`), (b) um `InternalEventType` novo, e (c) o Food ganhar um ramo para esse evento so para nao falhar `test_S4c_every_internal_event_has_a_consumer_branch` — tudo isso para um evento que so o Marketfy usaria hoje. O polling pontual via `GET /v1/subscriptions/{id}`, feito pelo consumidor quando o pagador volta do checkout (ou por um job de reconciliacao dele), resolve o mesmo problema sem tocar no vocabulario de webhook compartilhado com o Food. Ver [[mercadopago-adapter-contract]] e [[food-billing-subscription-contract]].

## Fora de escopo

- Qualquer mudanca no fluxo de checkout avulso (`/v1/payments`), que ja atende o consumidor.
- `payer_email` por requisicao: o `preapproval` já usa o e-mail do customer vinculado (criado com o e-mail da conta do usuario final), que é o comportamento certo — não há necessidade de sobrescrever.
- Mudanca de vocabulario de webhook ou de `InternalEventType`.

## Global Constraints (herdadas do plano de 2026-09-14)

- Nenhuma rota, payload ou header existente muda.
- Commits convencionais, terminando com `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Suite completa verde ao fim de cada task: `python -m pytest -q`.
- Mensagens de erro e logs em portugues sem acento.
