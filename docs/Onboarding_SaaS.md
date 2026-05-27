# Onboarding de Novo SaaS - Billing Core

## Objetivo

Este guia serve para conectar um novo produto da Neectify ao Billing Core sem duplicar logica de cobranca.

## Passos

1. Definir o `System` do novo produto.
2. Registrar o cliente interno em `INTERNAL_API_CLIENTS` com API key e scopes.
3. Definir o host interno permitido para callbacks em `ALLOWED_INTERNAL_WEBHOOK_HOSTS`.
4. Escolher os fluxos usados pelo produto: assinatura, pagamento avulso com customer ou checkout avulso via payment link.
5. Fazer o produto chamar `POST /v1/subscriptions`, `POST /v1/payments` ou `POST /v1/payment-links` com `Idempotency-Key`, conforme o fluxo.
6. Fazer o produto acompanhar o `job_id` por `GET /v1/jobs/{job_id}`.
7. Definir o contrato do webhook interno que o produto vai receber.

## O que o produto precisa enviar

### Para assinaturas

- `system`
- `system_sub_id`
- `customer_provider_id`
- `description`
- `value`
- `subscription_type`
- `expires_at`
- `webhook_link`

### Para checkout avulso via payment link

- `system`
- `system_payment_id`
- `description`
- `value`
- `billing_type` (recomendado: `UNDEFINED`)
- `due_date_limit_days`
- `webhook_link`

## Cuidados

- o `system` do payload precisa bater com o `X-System`
- o `webhook_link` precisa apontar para host permitido
- a API key do produto deve ter apenas os scopes necessarios

## Pagamentos avulsos

> [!IMPORTANT]
> **Escolha do endpoint:**
> Use `POST /v1/payment-links` para checkout avulso sem customer previo. Use `POST /v1/payments` somente quando o produto ja possui `customer_provider_id` e precisa criar uma cobranca vinculada a esse customer.

### Checkout avulso sem customer previo

Fluxo recomendado para creditos, pacotes e pedidos pontuais em que o comprador deve preencher CPF/CNPJ, nome e e-mail no checkout Asaas.

1. Chamar `POST /v1/payment-links` com `Idempotency-Key`.
2. Enviar `billing_type=UNDEFINED` para permitir que o comprador escolha PIX, boleto ou cartao.
3. Ler `job_id` e consultar `GET /v1/jobs/{job_id}` ate `status=completed`.
4. Redirecionar o usuario para `result.checkout_url`.
5. Receber webhook interno assinado com `event=PAYMENT_STATUS_UPDATED`.
6. Quando `payment_status=paid` ou `confirmed`, liberar o recurso comprado no SaaS.
7. Se necessario, consultar `GET /v1/payments/{payment_id}` respeitando 10 segundos entre chamadas para o mesmo pagamento.

Scopes minimos: `payments:create`, `payments:read`, `jobs:read`.

### Pagamento avulso legado com customer

1. **Obter o `customer_provider_id`:**
   - Verifique se o usuario ja possui um `customer_provider_id` salvo localmente no banco de dados do seu SaaS.
   - Se **nao possuir**, chame `POST /v1/customers` passando os dados cadastrais (CPF ou CNPJ) e salve o `provider_customer_id` retornado associado a esse usuario. O endpoint e idempotente: se o CPF/CNPJ ja existir no Asaas, ele retornara o ID do cadastro existente de forma segura.
   - Se **ja possuir**, reutilize o ID persistido localmente nas chamadas subsequentes sem chamar o endpoint de criacao novamente.
2. Chamar `POST /v1/payments` com `Idempotency-Key` enviando o `customer_provider_id`.
3. Ler `job_id` e consultar `GET /v1/jobs/{job_id}` ate obter o resultado.
4. Redirecionar o usuario para `checkout_url`.
5. Receber webhook interno assinado para mudancas de status.
6. Se necessario, consultar `GET /v1/payments/{payment_id}` respeitando 10 segundos entre chamadas para o mesmo pagamento.

Scopes minimos: `customers:create`, `payments:create`, `payments:read`, `jobs:read`.

## Quando um novo gateway entrar

O SaaS nao deve conhecer detalhes do gateway. A integracao continua com o Billing Core. O provider externo fica isolado na camada `app/infra/interfaces`.
