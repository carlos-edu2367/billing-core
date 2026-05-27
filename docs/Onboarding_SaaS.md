# Onboarding de Novo SaaS - Billing Core

## Objetivo

Este guia serve para conectar um novo produto da Neectify ao Billing Core sem duplicar logica de cobranca.

## Passos

1. Definir o `System` do novo produto.
2. Registrar o cliente interno em `INTERNAL_API_CLIENTS` com API key e scopes.
3. Definir o host interno permitido para callbacks em `ALLOWED_INTERNAL_WEBHOOK_HOSTS`.
4. Fazer o produto chamar `POST /v1/subscriptions` com `Idempotency-Key`.
5. Fazer o produto acompanhar o `job_id` por `GET /v1/jobs/{job_id}`.
6. Definir o contrato do webhook interno que o produto vai receber.

## O que o produto precisa enviar

- `system`
- `system_sub_id`
- `customer_provider_id`
- `description`
- `value`
- `subscription_type`
- `expires_at`
- `webhook_link`

## Cuidados

- o `system` do payload precisa bater com o `X-System`
- o `webhook_link` precisa apontar para host permitido
- a API key do produto deve ter apenas os scopes necessarios

## Pagamentos avulsos

> [!IMPORTANT]
> **Vínculo de Cliente (Asaas):**
> O campo `customer_provider_id` é obrigatório para chamar `POST /v1/payments`. O SaaS **deve** criar o cliente ou reutilizar o ID associado ao CPF/CNPJ.

1. **Obter o `customer_provider_id`:**
   - Verifique se o usuário já possui um `customer_provider_id` salvo localmente no banco de dados do seu SaaS.
   - Se **não possuir**, chame `POST /v1/customers` passando os dados cadastrais (CPF ou CNPJ) e salve o `provider_customer_id` retornado associado a esse usuário. O endpoint é idempotente: se o CPF/CNPJ já existir no Asaas, ele retornará o ID do cadastro existente de forma segura.
   - Se **já possuir**, reutilize o ID persistido localmente nas chamadas subsequentes sem chamar o endpoint de criação novamente.
2. Chamar `POST /v1/payments` com `Idempotency-Key` enviando o `customer_provider_id`.
3. Ler `job_id` e consultar `GET /v1/jobs/{job_id}` ate obter o resultado.
4. Redirecionar o usuario para `checkout_url`.
5. Receber webhook interno assinado para mudancas de status.
6. Se necessario, consultar `GET /v1/payments/{payment_id}` respeitando 10 segundos entre chamadas para o mesmo pagamento.

## Quando um novo gateway entrar

O SaaS nao deve conhecer detalhes do gateway. A integracao continua com o Billing Core. O provider externo fica isolado na camada `app/infra/interfaces`.
