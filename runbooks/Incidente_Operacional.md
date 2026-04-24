# Runbook - Incidente Operacional

## Quando usar

- aumento de erro HTTP
- jobs travados
- falha em webhooks
- fila crescendo acima do normal
- indisponibilidade parcial de banco ou Redis

## Checklist inicial

1. Verificar `/health`, `/ready` e `/metrics`.
2. Confirmar status do banco.
3. Confirmar status do Redis.
4. Validar se API e worker estao de pe.
5. Procurar `request_id` ou `job_id` no log estruturado.

## Evidencias para coletar

- horario do incidente
- `request_id`
- `job_id`
- `system`
- endpoint afetado
- status code
- erro do worker
- disponibilidade de banco e Redis

## Diagnostico rapido

### `/ready` degradado por Redis

- verificar conectividade
- validar credencial e saturacao
- checar se o worker ainda consegue ler a fila

### `/ready` degradado por banco

- validar pool e conexoes
- checar locks e tempo de resposta
- confirmar ultima migration aplicada

### Jobs em `retrying`

- checar erro final no metadata do job
- confirmar se a falha e transitoria ou permanente
- avaliar dead-letter

## Acao imediata

1. Se houver risco financeiro, pausar chamadas de criacao no consumidor.
2. Se o problema for gateway, seguir [Falha_Gateway.md](Falha_Gateway.md).
3. Se houver divergencia financeira, seguir [Reconciliacao_Financeira.md](Reconciliacao_Financeira.md).
4. Se for problema de deploy, seguir [Deploy_Rollback.md](Deploy_Rollback.md).

## Encerramento

- registrar causa raiz
- registrar impacto
- listar `request_id` e `job_id` relevantes
- abrir follow-up tecnico se o problema expuser fragilidade estrutural
