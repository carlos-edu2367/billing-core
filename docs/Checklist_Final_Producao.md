# Checklist Final de Producao - Billing Core

## Objetivo

Este documento consolida a validacao final antes do go-live. Ele deve ser executado para staging e para producao, sempre com registro de evidencias, responsavel, horario e resultado.

## Gate de liberacao

O deploy so pode seguir para producao quando todos os itens abaixo estiverem aprovados:

- seguranca validada sem findings criticos ou altos em aberto
- suite de testes verde
- migrations aplicadas e banco alinhado ao `head`
- observabilidade operacional validada
- deploy e rollback ensaiados
- secrets confirmados por ambiente
- readiness, liveness, health e metrics respondendo corretamente
- smoke tests pos-deploy aprovados
- runbooks e documentacao minima publicados e acessiveis

## Evidencias obrigatorias

- saida do preflight: `python scripts/preflight_production_check.py --run-tests --check-migrations`
- evidencia de migrations:
  `python -m alembic heads`
  `python -m alembic current`
- evidencias de endpoints:
  `GET /health`
  `GET /ready`
  `GET /live`
  `GET /metrics`
- evidencias de smoke test:
  `python scripts/post_deploy_smoke.py --base-url <url> --system <system> --api-key <api-key>`
- link para dashboard e alertas de API, Redis, banco e worker
- versao implantada, horario do deploy e responsavel

## Checklist de preflight

1. Confirmar `.env` e secrets do ambiente alvo.
2. Confirmar `DATABASE_URL`, `REDIS_URL`, `ASAAS_API_TOKEN`, `ASAAS_WEBHOOK_SECRET`, `INTERNAL_WEBHOOK_SIGNATURE` e `INTERNAL_API_CLIENTS`.
3. Confirmar que clientes usados em observabilidade possuem scope `metrics:read`.
4. Confirmar acesso do runtime ao PostgreSQL e Redis.
5. Rodar `python scripts/preflight_production_check.py --run-tests --check-migrations`.
6. Confirmar que `python -m alembic current` esta no mesmo revision de `python -m alembic heads`.
7. Confirmar que a API sobe sem erro e registra as rotas criticas.
8. Confirmar que worker sobe com retry, timeout e dead-letter configurados.
9. Validar dashboards, logs JSON, `X-Request-ID` e alertas minimos.
10. Revisar `runbooks/Deploy_Rollback.md` e criterios de abort.

## Checklist de deploy

1. Aplicar migrations no ambiente alvo: `python -m alembic upgrade head`.
2. Publicar API e worker na mesma versao.
3. Confirmar `GET /health`, `GET /ready`, `GET /live` e `GET /metrics`.
4. Executar `python scripts/post_deploy_smoke.py --base-url <url> --system <system> --api-key <api-key>`.
5. Verificar logs estruturados e ausencia de erros recorrentes nos primeiros minutos.
6. Verificar fila, retries e dead-letter apos o smoke test.
7. Registrar evidencias do deploy.

## Criterios de abort e rollback

Abortar ou reverter imediatamente se qualquer um dos pontos abaixo acontecer:

- `/ready` retornar `degraded`
- erro consistente de bootstrap da API ou worker
- migrations nao convergirem para o `head`
- falha massiva na criacao de assinatura
- falha de autenticacao ou webhook em comportamento inesperado
- aumento anormal de erros 5xx, retries ou dead-letter

Usar o procedimento documentado em [runbooks/Deploy_Rollback.md](../runbooks/Deploy_Rollback.md).

## Checklist de aprovacao final

| Item | Status | Evidencia | Responsavel |
| --- | --- | --- | --- |
| Seguranca final validada | Pendente |  |  |
| Testes aprovados | Pendente |  |  |
| Migrations validadas em staging | Pendente |  |  |
| Observabilidade e alertas validados | Pendente |  |  |
| Deploy e rollback ensaiados | Pendente |  |  |
| Secrets e ambientes confirmados | Pendente |  |  |
| Gateway em modo alvo validado | Pendente |  |  |
| Health, readiness e liveness aprovados | Pendente |  |  |
| Runbooks e documentacao publicados | Pendente |  |  |
| Smoke test pos-deploy aprovado | Pendente |  |  |

## Go-live

O go-live so deve ser autorizado quando a tabela acima estiver 100% preenchida e sem pendencias abertas. Se algum item depender de aceite manual externo, isso deve ficar registrado explicitamente antes da liberacao.
