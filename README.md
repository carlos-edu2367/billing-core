# Billing Core

> Este projeto faz parte do ecossistema da Neectify e foi desenvolvido como uma das bases da iniciativa.

Billing Core é um backend orientado à produção para cobrança recorrente em produtos SaaS. Ele centraliza criação de assinaturas, integração com gateway, recepção de webhooks, processamento assíncrono, rastreabilidade por job e proteções operacionais como idempotência, rate limit, logs estruturados, readiness checks e runbooks de deploy.

Este projeto foi construído como um backend open source pessoal para demonstrar como projetar um serviço financeiro crítico com decisões pragmáticas de arquitetura, preocupação real com operação e um caminho claro entre desenvolvimento local e deploy.

## Por Que Este Projeto Existe

Em muitos produtos SaaS, a lógica de cobrança começa espalhada entre vários serviços ou fica fortemente acoplada a um único gateway de pagamento. Isso normalmente gera três problemas:

- as regras de cobrança ficam difíceis de evoluir com segurança
- retries e webhooks geram efeitos colaterais duplicados
- a visibilidade operacional é fraca exatamente onde o risco de negócio é maior

O Billing Core resolve isso isolando billing em um serviço dedicado, com contratos explícitos, fluxos assíncronos e preocupações de infraestrutura tratadas como parte do desenho da aplicação.

## O Que O Projeto Cobre

- API HTTP em FastAPI com rotas versionadas em `/v1`
- criação assíncrona de assinatura com orquestração via Redis
- recepção de webhook com validação de secret, replay protection e deduplicação
- autenticação server-to-server via `X-System` e `X-API-Key`
- autorização baseada em scopes para consumidores internos
- tratamento idempotente para operações críticas de escrita
- workers com retry, metadata de job e suporte a dead-letter
- persistência em PostgreSQL com SQLAlchemy e migrations via Alembic
- logs estruturados em JSON, correlation IDs e endpoints operacionais
- scripts voltados à produção para preflight e smoke test pós-deploy
- testes cobrindo regras de domínio, contratos da API e fluxos críticos

## Arquitetura Em Alto Nível

O projeto segue uma arquitetura em camadas com responsabilidades bem definidas:

- `app/domain`: entidades, enums, value objects e regras de negócio
- `app/application`: casos de uso, DTOs, interfaces e contratos de repositório
- `app/infra`: banco, integrações externas, repositórios, config, observabilidade e jobs
- `app/web`: camada HTTP, dependências, schemas, rotas e tratamento de erros
- `app/workers`: execução assíncrona com ARQ

Essa separação mantém as regras de negócio independentes de FastAPI, Redis e do provider de gateway. Na prática, isso torna o código mais fácil de testar, mais seguro de refatorar e mais simples de adaptar para outros cenários de cobrança.

## Principais Decisões De Arquitetura

### 1. Escritas críticas são processadas de forma assíncrona

A criação de assinatura não bloqueia a requisição HTTP até todas as etapas externas terminarem. Em vez disso, a API enfileira o trabalho e retorna um `job_id`. Isso melhora a resiliência sob latência, deixa retries mais previsíveis e reduz o risco de timeout em operações sensíveis.

### 2. Idempotência é requisito de sistema, não remendo

Fluxos críticos usam `Idempotency-Key`, hash de payload, identidade técnica de webhook, janela de replay e dedupe de operação no gateway. Em billing, efeitos colaterais duplicados são uma das formas mais rápidas de gerar prejuízo ou perder confiança, então idempotência aqui faz parte do desenho da arquitetura.

### 3. Regras de domínio ficam fora da camada web

As regras de negócio vivem nas entidades de domínio e nos casos de uso, não dentro das rotas HTTP. Isso ajuda a manter o comportamento mais determinístico e testável sem depender do framework para validar a lógica principal.

### 4. Integrações internas são explícitas e restritas

O serviço foi pensado para comunicação máquina a máquina. Clientes autenticam com `X-System` e `X-API-Key`, recebem apenas os scopes necessários e podem ser limitados por hosts permitidos para webhooks internos. Isso deixa o modelo de integração mais simples e mais seguro para um core interno de billing.

### 5. Prontidão para produção faz parte do código

Este repositório inclui readiness e liveness, smoke tests, preflight, runbooks, migrations e orientações de deploy. A ideia não é só ter código que funciona localmente, mas sim um serviço que pode ser operado com previsibilidade.

## Stack

- Python 3.13
- FastAPI
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Redis
- ARQ
- HTTPX
- Pydantic 2
- Docker / Docker Compose
- Pytest

## Fluxos Principais

### Criação de assinatura

1. Um sistema interno envia `POST /v1/subscriptions`
2. A requisição é autenticada e limitada por rate limit
3. A idempotência é verificada antes de enfileirar o job
4. Um worker cria a assinatura no gateway
5. O espelho local de assinatura e pagamento é persistido
6. O cliente acompanha o progresso com `GET /v1/jobs/{job_id}`

### Processamento de webhook

1. O gateway chama `POST /v1/webhooks/asaas`
2. O secret do webhook é validado
3. São aplicadas proteção contra replay e identificação técnica do evento
4. O processamento acontece de forma assíncrona
5. O estado local de pagamento e assinatura é atualizado
6. Um webhook interno pode ser disparado para sistemas downstream

## Como Adaptar Este Projeto Para O Seu Caso

Este repositório é opinativo, mas foi feito para ser reutilizável.

Você pode adaptá-lo se quiser construir:

- um serviço de billing para seu próprio SaaS
- um orquestrador central de pagamentos para vários produtos internos
- uma camada segura de integração em cima de um gateway de pagamento
- um projeto de portfólio backend com preocupação real de arquitetura e operação

Os principais pontos de adaptação são:

- `System` e `INTERNAL_API_CLIENTS`: definem os consumidores internos
- `ALLOWED_INTERNAL_WEBHOOK_HOSTS`: restringem quem pode receber callbacks internos
- implementação do gateway em `app/infra/interfaces`: troque o Asaas ou adicione outro provider
- entidades e casos de uso: inclua suas regras de cobrança, planos, conciliação e cancelamento
- schemas de request e response: ajuste o contrato da API ao seu produto
- modelo de deploy: troque Docker Compose pela sua plataforma preferida, como ECS, Kubernetes, Railway, Fly.io ou Render

Se você quiser reaproveitar a estrutura em outro domínio, o caminho mais seguro é:

1. manter os limites entre as camadas
2. substituir a integração específica do gateway
3. adaptar contratos e regras de domínio
4. preservar os mecanismos operacionais de segurança

## Como Rodar Localmente

### 1. Clonar e configurar

Use `.env.example` como ponto de partida:

```powershell
Copy-Item .env.example .env
```

Depois ajuste os valores necessários em `.env`, especialmente:

- `DATABASE_URL`
- `REDIS_URL`
- `ASAAS_API_TOKEN`
- `ASAAS_WEBHOOK_SECRET`
- `INTERNAL_WEBHOOK_SIGNATURE`
- `INTERNAL_API_CLIENTS`
- `ALLOWED_INTERNAL_WEBHOOK_HOSTS`

### 2. Instalar dependências

```powershell
python -m pip install -r requirements.txt
```

### 3. Aplicar migrations

```powershell
python -m alembic upgrade head
```

### 4. Subir a API

```powershell
python -m app.web.main
```

### 5. Subir o worker

```powershell
python -m app.workers.worker
```

## Como Rodar Com Docker

Para subir API e worker com Docker Compose:

```powershell
docker compose up --build billing-core-api billing-core-worker
```

API e worker rodam como serviços separados, o que aproxima melhor o ambiente local de um deploy real em produção.

## Testes E Validações

Rodar a suíte automatizada:

```powershell
python -m pytest -q
```

Rodar o checklist de preflight:

```powershell
python scripts/preflight_production_check.py --run-tests --check-migrations
```

Rodar o smoke test pós-deploy:

```powershell
python scripts/post_deploy_smoke.py --base-url https://billing.example.com --system neectify_shop --api-key <api-key>
```

## Observações De Produção

- use secrets por ambiente, nunca credenciais hardcoded
- desabilite docs da API em produção com `ENABLE_API_DOCS=false`
- publique API e worker como processos ou containers separados
- mantenha PostgreSQL e Redis gerenciados e monitorados
- execute migrations antes ou durante o deploy de forma controlada
- restrinja `metrics:read` apenas a clientes operacionais
- rotacione periodicamente segredos internos e segredos de webhook

## Guia Do Repositório

- [docs/Arquitetura.md](docs/Arquitetura.md)
- [docs/API.md](docs/API.md)
- [docs/Webhooks.md](docs/Webhooks.md)
- [docs/Ambiente.md](docs/Ambiente.md)
- [docs/Fluxos.md](docs/Fluxos.md)
- [docs/Onboarding_SaaS.md](docs/Onboarding_SaaS.md)
- [docs/Checklist_Final_Producao.md](docs/Checklist_Final_Producao.md)
- [runbooks/Incidente_Operacional.md](runbooks/Incidente_Operacional.md)
- [runbooks/Falha_Gateway.md](runbooks/Falha_Gateway.md)
- [runbooks/Reconciliacao_Financeira.md](runbooks/Reconciliacao_Financeira.md)
- [runbooks/Deploy_Rollback.md](runbooks/Deploy_Rollback.md)

Se você está lendo este repositório como recrutador(a) ou gestor(a), a ideia aqui é mostrar mais do que CRUD. Este projeto destaca:

- arquitetura backend com limites bem definidos
- cuidado com fluxos financeiros críticos
- preocupação prática com segurança e operação
- testes e validações pensados para produção
- capacidade de projetar para extensibilidade, e não apenas para o caminho feliz

É o tipo de projeto que eu construo quando quero que o código seja lido, operado, adaptado e confiável.
