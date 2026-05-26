# Arquitetura - Billing Core

## Visao geral

O Billing Core foi estruturado em camadas para manter o dominio de billing separado da borda HTTP e das integracoes externas.

### Camadas atuais

| Camada | Pasta | Responsabilidade |
| --- | --- | --- |
| Web | `app/web` | HTTP, auth, rate limit, contratos publicos e health checks |
| Application | `app/application` | casos de uso, DTOs e contratos de gateway/repositorio |
| Domain | `app/domain` | entidades, enums, regras e value objects |
| Infra | `app/infra` | banco, repositorios, providers, config, observabilidade |
| Workers | `app/workers` | processamento assíncrono com ARQ |

## Componentes principais

### Dominio

- `Customer`
- `Subscription`
- `Payment`
- `WebhookEvent`

### Casos de uso

- `CreateCustomer`
- `CreateSubscription`
- `CancelSubscription`
- `ProcessWebhookService`

### Integrações

- gateway atual: Asaas
- webhook interno assinado para sistemas Neectify
- contrato de gateway via `InterfaceGateway`, incluindo criacao, cancelamento e verificacao de status de assinatura

## Fluxo macro

1. Um sistema interno chama `POST /v1/subscriptions`.
2. A API valida autenticacao, escopo, payload e idempotencia.
3. A requisicao vira job ARQ.
4. O worker executa `CreateSubscription`.
5. O gateway cria a assinatura e o primeiro pagamento.
6. O core persiste `Subscription` e `Payment`.
7. O sistema consumidor acompanha o status por `GET /v1/jobs/{job_id}`.

Fluxo de cancelamento:

1. Um sistema interno chama `POST /v1/subscriptions/{subscription_id}/cancel`.
2. A API valida autenticacao, escopo, ownership da assinatura e idempotencia.
3. A requisicao vira job ARQ.
4. O worker executa `CancelSubscription`.
5. O core marca a assinatura como `cancellation_pending`.
6. O adapter do gateway verifica o estado remoto e executa o cancelamento quando necessario.
7. O core persiste o estado final `canceled` sem acoplar o dominio ao provider concreto.

Fluxo de webhook:

1. O Asaas chama `POST /v1/webhooks/asaas`.
2. O Billing Core valida o header `asaas-access-token`.
3. O payload cru e normalizado e enfileirado.
4. O worker executa `ProcessWebhookService`.
5. O evento e deduplicado por `WebhookEvent`.
6. O core atualiza `Payment` e `Subscription`.

## Dependencias operacionais

- PostgreSQL
- Redis
- ARQ
- FastAPI
- SQLAlchemy async

## Decisoes atuais importantes

- auth interna por API key + `X-System`
- escopo por cliente interno em `INTERNAL_API_CLIENTS`
- idempotencia de criacao e cancelamento de assinatura em Redis
- dedupe adicional de operacao externa em `gateway_operations`
- metadata de job em Redis
- logs estruturados em JSON

## Limites atuais

- o core ainda esta focado em assinaturas
- nao existe ainda fluxo publico de pagamentos avulsos
- existe apenas um gateway implementado
- nao existe tracing distribuido nem dashboard externo nativo no repositorio
