# Onboarding de Novo SaaS - Billing Core

## Passos

1. Defina o `System` do produto e registre-o em `INTERNAL_API_CLIENTS` com os scopes mínimos.
2. Permita o host do callback assinado em `ALLOWED_INTERNAL_WEBHOOK_HOSTS`.
3. Para assinaturas, crie e persista o customer com `POST /v1/customers`.
4. Para venda avulsa, chame `POST /v1/payments` com `Idempotency-Key`; essa rota cria um checkout Asaas.
5. Consulte `GET /v1/jobs/{job_id}` até o resultado incluir a URL do checkout.
6. Configure e valide o webhook interno assinado antes de liberar qualquer recurso.

## Checkout avulso

Envie `value`, `description`, `system`, `system_payment_id`, `webhook_link`, `minutes_to_expire`, `items`, `success_url`, `cancel_url` e `expired_url`. O item precisa ter referência externa, nome, descrição, quantidade e valor; a soma dos itens deve bater com `value`.

As três URLs de retorno devem usar hosts permitidos por `ALLOWED_CHECKOUT_REDIRECT_HOSTS`. Elas são apenas navegação de interface: não liberam créditos, pedidos ou acesso.

O produto deve aguardar a atualização assinada produzida após `CHECKOUT_PAID` para conceder o benefício. `CHECKOUT_CREATED`, `CHECKOUT_CANCELED` e `CHECKOUT_EXPIRED` atualizam somente o estado do checkout.

Scopes mínimos: `payments:create`, `payments:read`, `jobs:read`.

## Assinaturas

Assinaturas continuam exigindo `customer_provider_id`, `subscriptions:create` e `customers:create`. O checkout avulso não exige customer prévio.

## Cuidados

- o `system` do payload deve coincidir com `X-System`;
- o `webhook_link` deve apontar para host permitido;
- a API key deve ter somente os scopes necessários;
- valide sempre a assinatura HMAC do callback interno e processe cada evento de forma idempotente.
