# Ambiente e Configuracao - Billing Core

## Variaveis principais

| Variavel | Uso |
| --- | --- |
| `SERVICE_NAME` | nome logico do servico |
| `APP_ENV` | ambiente atual |
| `LOG_LEVEL` | nivel de log |
| `HOST` | bind da API |
| `PORT` | porta da API |
| `DATABASE_URL` | conexao do PostgreSQL |
| `REDIS_URL` | conexao do Redis |
| `ASAAS_API_TOKEN` | token de API do Asaas |
| `ASAAS_WEBHOOK_SECRET` | validacao do webhook Asaas |
| `INTERNAL_WEBHOOK_SIGNATURE` | assinatura HMAC dos webhooks internos |
| `INTERNAL_API_CLIENTS` | clientes internos com API key e scopes |
| `CORS_ALLOW_ORIGINS` | origens permitidas para CORS |
| `ALLOWED_INTERNAL_WEBHOOK_HOSTS` | hosts aceitos para `webhook_link` |
| `ALLOWED_CHECKOUT_REDIRECT_HOSTS` | hosts aceitos para `success_url`, `cancel_url` e `expired_url` |
| `ENABLE_API_DOCS` | habilita ou desabilita `/docs`, `/redoc` e `/openapi.json` |
| `MAX_REQUEST_BODY_BYTES` | limite maximo global de payload HTTP |
| `MAX_WEBHOOK_BODY_BYTES` | limite maximo permitido para payloads de webhook |

## Banco

| Variavel | Uso |
| --- | --- |
| `DB_POOL_SIZE` | tamanho base do pool |
| `DB_MAX_OVERFLOW` | conexoes extras alem do pool |
| `DB_POOL_TIMEOUT_SECONDS` | tempo maximo aguardando conexao |
| `DB_POOL_RECYCLE_SECONDS` | reciclagem do pool |
| `DB_STATEMENT_TIMEOUT_MS` | timeout de statement no banco |

## Workers

| Variavel | Uso |
| --- | --- |
| `WORKER_JOB_TIMEOUT_SECONDS` | timeout de job |
| `WORKER_KEEP_RESULT_SECONDS` | retencao de resultado no ARQ |
| `WORKER_MAX_TRIES` | numero maximo de tentativas |
| `WORKER_RETRY_BACKOFF_SECONDS` | backoff base |
| `WORKER_DEAD_LETTER_TTL_SECONDS` | TTL de dead-letter em Redis |
| `WORKER_MAX_JOBS` | concorrencia do worker |

## Rate limit e idempotencia

| Variavel | Uso |
| --- | --- |
| `INTERNAL_RATE_LIMIT_REQUESTS` | limite das rotas internas |
| `INTERNAL_RATE_LIMIT_WINDOW_SECONDS` | janela do limite interno |
| `WEBHOOK_RATE_LIMIT_REQUESTS` | limite do webhook |
| `WEBHOOK_RATE_LIMIT_WINDOW_SECONDS` | janela do webhook |
| `WEBHOOK_REPLAY_TTL_SECONDS` | janela de replay de webhook |
| `WEBHOOK_PROCESSING_LOCK_TTL_SECONDS` | lock de processamento do evento |
| `JOB_METADATA_TTL_SECONDS` | TTL dos metadados de job |
| `SUBSCRIPTION_IDEMPOTENCY_TTL_SECONDS` | TTL da chave de idempotencia |

## Politica de secrets

- nao commitar valores reais em producao
- separar credenciais por ambiente
- rotacionar `ASAAS_API_TOKEN`, `ASAAS_WEBHOOK_SECRET` e API keys internas
- manter `ASAAS_WEBHOOK_SECRET` e `INTERNAL_WEBHOOK_SIGNATURE` com pelo menos 32 caracteres e sem espacos
- restringir acesso a `.env` somente ao runtime e CI autorizados
- em producao, rejeitar placeholders e manter `ENABLE_API_DOCS=false`

## Scopes internos

- `customers:create`
- `subscriptions:create`
- `subscriptions:cancel`
- `payments:create`
- `payments:read`
- `jobs:read`
- `metrics:read`

`payments:create` cobre `POST /v1/payments`, que cria checkout avulso. Esse fluxo não exige `customers:create`; assinaturas continuam exigindo esse escopo.

## Checkout Asaas

Em produção, configure `ALLOWED_CHECKOUT_REDIRECT_HOSTS` com os hosts HTTPS de retorno de cada produto. Configure o webhook Asaas para `CHECKOUT_CREATED`, `CHECKOUT_CANCELED`, `CHECKOUT_EXPIRED` e `CHECKOUT_PAID`. Callbacks de navegador não confirmam pagamento: somente `CHECKOUT_PAID` pode liberar benefícios.

## Bootstrap recomendado

```powershell
python -m alembic upgrade head
python -m app.web.main
python -m app.workers.worker
```
