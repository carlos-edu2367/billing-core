# Guia de Integração — Billing Core

> Base URL de produção: `https://api.billing.neectify.com`

## Criar checkout

`POST /v1/payments` cria um checkout no gateway padrão (Mercado Pago) de modo assíncrono. Envie os headers `X-System`, `X-API-Key` e `Idempotency-Key`.

```json
{
  "value": "72.00",
  "description": "Créditos NF-e - pack_100",
  "system": "marketfy",
  "system_payment_id": "550e8400-e29b-41d4-a716-446655440000",
  "webhook_link": "https://api-marketfy.neectify.com/api/v1/webhooks/billing-core",
  "minutes_to_expire": 30,
  "items": [{
    "external_reference": "550e8400-e29b-41d4-a716-446655440000",
    "name": "100 créditos NF-e",
    "description": "Créditos para emissão fiscal",
    "quantity": 1,
    "value": "72.00"
  }],
  "success_url": "https://app.marketfy.com/billing/success",
  "cancel_url": "https://app.marketfy.com/billing/cancel",
  "expired_url": "https://app.marketfy.com/billing/expired"
}
```

O retorno imediato é `202` com `job_id`. Consulte `GET /v1/jobs/{job_id}` até `completed`; o resultado contém a URL segura do checkout. Repetir a mesma chave e o mesmo corpo retorna o mesmo job; reutilizar a chave com corpo distinto retorna `409`.

O checkout não recebe customer prévio nem dados de cobrança direta. `minutes_to_expire` deve ficar entre 10 e 1440; a soma de `items` deve igualar `value`.

Com Mercado Pago, a vigência mínima efetiva do checkout é 30 minutos (mínimo aceito pelo Pix do gateway) — um `minutes_to_expire` menor é estendido internamente pelo adapter. `expired_url` não é usado nesse gateway: o Checkout Pro não redireciona o comprador em caso de expiração; a expiração é detectada pelo cron de sincronização (ver Go-live).

## Configuração

O cliente interno deve ter `payments:create`, `payments:read` e `jobs:read`. Registre o host do callback assinado em `ALLOWED_INTERNAL_WEBHOOK_HOSTS` e os hosts HTTPS das três URLs em `ALLOWED_CHECKOUT_REDIRECT_HOSTS`.

Assinaturas continuam em `POST /v1/subscriptions` e exigem customer criado previamente em `POST /v1/customers`. Com Mercado Pago, o `checkout_url` devolvido pelo job é o link para o pagador autorizar o cartão (`init_point` do `preapproval`); a assinatura só fica `active` depois que a primeira fatura é aprovada, cerca de 1 hora após a autorização. `back_url` é opcional; quando enviado, precisa estar em `ALLOWED_CHECKOUT_REDIRECT_HOSTS` e substitui `MERCADOPAGO_SUBSCRIPTION_BACK_URL` só para essa assinatura. `GET /v1/subscriptions/{id}` consulta o status ao vivo no gateway (scope `subscriptions:read`) — útil para saber se o cartão já foi autorizado antes da primeira fatura chegar.

O gateway usado para customers, checkouts e assinaturas **novos** é controlado por `DEFAULT_GATEWAY_PROVIDER` (`mercadopago` por padrão). Registros já criados no Asaas continuam operados pelo Asaas — o gateway fica gravado por registro e não muda retroativamente.

## Evento e liberação de benefício

As URLs `success_url`, `cancel_url` e `expired_url` são retornos de navegador, não uma fonte de verdade financeira. Nunca conceda créditos, acesso ou pedido a partir delas.

Configure os eventos técnicos do gateway em uso. O Billing Core deduplica os recebimentos e entrega a atualização interna assinada ao `webhook_link`.

| Evento Asaas | Tópico Mercado Pago | Estado local | Libera benefício |
| --- | --- | --- | --- |
| `CHECKOUT_CREATED` | — (preferência criada, sem notificação) | `pending` | não |
| `CHECKOUT_CANCELED` | `payment` cancelado/rejeitado (não gera evento; fica pendente até expirar) | `canceled` | não |
| `CHECKOUT_EXPIRED` | detectado pelo cron `sync_pending_checkouts_worker` | `expired` | não |
| `CHECKOUT_PAID` | `payment` aprovado | `paid` | sim |
| `PAYMENT_REFUNDED` | `payment` `refunded` | `refunded` | — |

Valide `X-Webhook-Signature-256` com o segredo compartilhado antes de processar uma entrega interna. Aplique a operação de forma idempotente usando a referência do seu pedido/pacote.

## Go-live

1. Configure os hosts permitidos e os eventos técnicos do gateway padrão. Para o Mercado Pago: em Suas integrações > Webhooks > Configurar notificação, marque os tópicos **Pagamentos** e **Planos e assinaturas**, aponte para `/v1/webhooks/mercadopago` e guarde a assinatura secreta em `MERCADOPAGO_WEBHOOK_SECRET`.
2. Publique API e worker do Billing Core juntos; depois publique o consumidor.
3. Em Sandbox, crie checkout PIX e cartão, repita a chave de idempotência, consulte o job e abra a URL.
4. Confirme que os retornos de navegador não concedem acesso e que apenas `CHECKOUT_PAID` o faz.
5. Monitore jobs em dead letter, operações em reconciliação, deduplicação de webhooks e entregas internas. Com Mercado Pago, monitore também a execução do cron `sync_pending_checkouts_worker` (a cada 5 minutos).
