# Migração de Payments para Asaas Checkout — Design

**Data:** 2026-07-20

## Objetivo

Transformar `POST /v1/payments` no único fluxo de checkout avulso do Billing Core. Em vez de criar uma cobrança Asaas vinculada a um `customer_provider_id`, a rota criará um Asaas Checkout do tipo `DETACHED`, retornará seu link pelo job assíncrono e atualizará o pagamento local por eventos `CHECKOUT_*`.

`POST /v1/payment-links` e toda a implementação de payment links serão removidos.

## Contexto e decisão

Hoje há dois fluxos de venda avulsa:

- `POST /v1/payments` cria `POST /v3/payments`, exige um customer já criado e agenda polling de uma cobrança.
- `POST /v1/payment-links` cria `POST /v3/paymentLinks`, sem customer, mas não oferece o contrato de checkout com itens, expiração e callbacks.

O Asaas Checkout é o primitivo correto para a jornada de compra hospedada: aceita `billingTypes`, `chargeTypes`, `minutesToExpire`, `items`, `callback` e `externalReference`; devolve `id`, `link` e estado inicial `ACTIVE`. A criação de checkout nunca confirma o pagamento. A confirmação é feita por webhook.

O endpoint público seguirá sendo `POST /v1/payments`, pois este é o contrato definido para o fluxo de compra comum. Não será criada uma rota concorrente `POST /v1/checkouts`.

## Escopo

### Incluído

- Migrar `POST /v1/payments` para criar `POST /v3/checkouts` com `chargeTypes: ["DETACHED"]`.
- Exigir itens, callbacks HTTPS permitidos e expiração de 10 a 1440 minutos.
- Usar PIX e cartão de crédito como meios de pagamento do checkout v1.
- Persistir o checkout no modelo `Payment` existente.
- Processar os eventos `CHECKOUT_CREATED`, `CHECKOUT_PAID`, `CHECKOUT_CANCELED` e `CHECKOUT_EXPIRED`.
- Manter o webhook interno já consumido pelos produtos, com evento `PAYMENT_STATUS_UPDATED`.
- Remover toda a superfície de `payment-links`.
- Migrar o cliente Marketfy que ainda chama `POST /v1/payment-links`.
- Atualizar testes, OpenAPI e documentação de integração.

### Excluído

- Checkout recorrente, parcelamento e split.
- Cobrança direta por cartão, coleta de dados de cartão e qualquer confirmação financeira síncrona.
- Mudança de schema ou criação de tabela nova: o modelo `Payment` existente comporta o checkout.
- Alterar os fluxos de assinatura.

## Contrato público

`POST /v1/payments` continuará autenticado com `payments:create`, `X-System`, `X-API-Key` e `Idempotency-Key`, e responderá `202 Accepted` com `job_id`.

O request será substituído por:

```json
{
  "system": "marketfy",
  "system_payment_id": "order-123",
  "description": "Pacote de 100 créditos NF-e",
  "value": "72.00",
  "minutes_to_expire": 30,
  "items": [
    {
      "external_reference": "pack-100",
      "name": "100 créditos NF-e",
      "description": "Créditos para emissão fiscal",
      "quantity": 1,
      "value": "72.00"
    }
  ],
  "success_url": "https://app.marketfy.com/billing/success",
  "cancel_url": "https://app.marketfy.com/billing/cancel",
  "expired_url": "https://app.marketfy.com/billing/expired",
  "webhook_link": "https://api.marketfy.neectify.com/api/v1/webhooks/billing-core"
}
```

Campos removidos: `customer_provider_id`, `due_date` e `billing_type`. O v1 fixa `billingTypes` em `["PIX", "CREDIT_CARD"]`; assim, o pagador escolhe o meio no checkout e o tipo real fica conhecido pelo evento financeiro posterior.

Validações obrigatórias:

- `value > 0` e exatamente igual à soma de `item.quantity * item.value`.
- Pelo menos um item; nome não vazio, quantidade inteira positiva e valor positivo.
- `minutes_to_expire` entre 10 e 1440.
- `system` igual ao sistema autenticado.
- URLs de callback e webhook com HTTPS.
- Callbacks restritos a `ALLOWED_CHECKOUT_REDIRECT_HOSTS`; webhook restrito a `ALLOWED_INTERNAL_WEBHOOK_HOSTS`.
- `system_payment_id` único dentro de `system`.
- `external_reference` calculada como `checkout:{system}:{system_payment_id}`, limitada a 200 caracteres.

O resultado do job será:

```json
{
  "payment_id": "uuid-local",
  "checkout_url": "https://sandbox.asaas.com/checkoutSession/show/checkout-id",
  "payment_status": "pending"
}
```

## Arquitetura e fluxo

```text
Produto -> POST /v1/payments -> ARQ create_checkout_worker
        -> CreateCheckout -> Asaas POST /v3/checkouts
        -> Payment local (checkout_id, link, pending)
        -> GET /v1/jobs/{job_id} -> checkout_url

Pagador -> link do Asaas Checkout
Asaas -> POST /v1/webhooks/asaas (CHECKOUT_*)
      -> ProcessWebhook -> Payment local atualizado
      -> webhook interno assinado -> produto
```

### Gateway

Substituir `CreatePaymentGatewayResponse` e `CreatePaymentLinkGatewayResponse` usados no fluxo avulso por `CreateCheckoutGatewayResponse(checkout_id, checkout_url, status, external_reference)`.

`AsaasProvider.create_checkout` enviará:

```json
{
  "billingTypes": ["PIX", "CREDIT_CARD"],
  "chargeTypes": ["DETACHED"],
  "minutesToExpire": 30,
  "externalReference": "checkout:marketfy:order-123",
  "callback": {
    "successUrl": "...",
    "cancelUrl": "...",
    "expiredUrl": "..."
  },
  "items": ["..."]
}
```

Usará o campo `link` retornado pela API. Ausência de `id`, `link`, `status` ou `externalReference` na resposta será uma falha do gateway e não poderá criar estado local parcial.

### Persistência e idempotência

O registro `Payment` manterá o papel de agregado local:

- `provider_payment_id = checkout_id`;
- `checkout_link = link`;
- `external_reference = checkout:{system}:{system_payment_id}`;
- `payment_status = pending`;
- `payment_type = UNDEFINED` até haver informação financeira aplicável.

O namespace Redis será `checkout_create`, e `GatewayOperation` usará `operation_name` e chave de deduplicação `create_checkout:{system}:{system_payment_id}`. As regras existentes se preservam: operação concluída sem espelho local exige reconciliação; operação em reconciliação bloqueia nova criação; operação falha sem referência pode ser repetida com segurança.

Não haverá agendamento de `reconcile_pending_payment_worker`: ele consulta uma cobrança por ID e não um checkout. O caminho autoritativo é o webhook do checkout.

## Webhooks e estados

O normalizador Asaas reconhecerá objetos `checkout`, preservará o ID do evento do Asaas e produzirá uma referência estável para idempotência. O processamento localizará o `Payment` primeiro pelo `checkout_id` e, quando necessário, pelo `externalReference`.

| Evento Asaas | Efeito local | Webhook interno |
|---|---|---|
| `CHECKOUT_CREATED` | mantém `pending`; sem notificação | não envia |
| `CHECKOUT_PAID` | marca como `paid` | envia `PAYMENT_STATUS_UPDATED` |
| `CHECKOUT_CANCELED` | marca como `canceled` | envia `PAYMENT_STATUS_UPDATED` |
| `CHECKOUT_EXPIRED` | marca como `expired` | envia `PAYMENT_STATUS_UPDATED` |

Eventos duplicados não poderão gerar transições ou notificações duplicadas. Eventos desconhecidos ou sem pagamento local serão registrados de forma observável e reconhecidos sem falhar o endpoint de webhook. A URL de callback nunca é usada para liberar produto; apenas o webhook assinado pode fazer isso.

## Remoções e compatibilidade

Será removido todo código de `payment-links`: rota, schema, DTOs, use case, resposta/interface de gateway, worker, registro do worker, testes e documentação.

`POST /v1/payments` terá breaking change no payload. O único consumidor ativo identificado, Marketfy, será migrado na mesma entrega; não será mantido adaptador implícito para payload antigo, pois isso perpetuaria o fluxo incorreto. A consulta `GET /v1/payments/{payment_id}` permanece e continua expondo o estado local.

Jobs antigos de `create_payment_worker` e `create_payment_link_worker` devem ser drenados antes de remover seus handlers. API, worker e cliente Marketfy devem ser publicados de forma coordenada.

## Garantias de correção

- O payload ao Asaas será validado por testes de provider, incluindo nomes de campos, meios, `DETACHED`, expiração, callbacks e itens.
- Testes de contrato cobrirão todas as rejeições de entrada e a resposta idempotente da rota.
- Testes do caso de uso cobrirão sucesso, reutilização local, retries seguros e falhas parciais marcadas para reconciliação.
- Testes de webhook cobrirão os quatro eventos, eventos duplicados, checkout desconhecido e as notificações internas.
- Teste de integração do Marketfy cobrirá a criação, polling do job, redirecionamento e atualização por webhook.
- Antes do rollout, executar checkout PIX e cartão no Sandbox; confirmar callbacks, cada evento `CHECKOUT_*`, assinatura do webhook e ausência de liberação pelo callback.

## Arquivos afetados

Criar: DTOs/schemas/use case/rota/testes de checkout.

Modificar: interface e provider Asaas, processamento e DTO de webhook, worker e registro, configuração, modelo OpenAPI, documentação e cliente Marketfy.

Remover: todos os arquivos e referências de payment links; fluxo de criação direta de cobrança avulsa e sua reconciliação agendada.

## Critérios de aceite

1. `POST /v1/payments` cria somente um Checkout Asaas `DETACHED` com PIX e cartão.
2. A rota não aceita nem precisa de customer Asaas.
3. O job devolve o link do Checkout retornado pelo Asaas.
4. Nenhum código ativo de payment links ou cobrança avulsa direta permanece.
5. `CHECKOUT_PAID`, cancelamento e expiração atualizam exatamente um `Payment` e produzem, no máximo, um webhook interno aplicável.
6. O Marketfy utiliza o novo contrato e consegue completar uma compra de créditos no Sandbox.
