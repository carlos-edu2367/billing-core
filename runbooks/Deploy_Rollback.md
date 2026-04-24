# Runbook - Deploy e Rollback

## Pre-deploy

1. Validar `.env` do ambiente alvo.
2. Validar conectividade com banco e Redis.
3. Rodar testes:

```powershell
python -m pytest -q
```

4. Validar migrations pendentes:

```powershell
python -m alembic heads
python -m alembic current
```

## Deploy

1. Aplicar migrations:

```powershell
python -m alembic upgrade head
```

2. Subir API.
3. Subir worker.
4. Validar:

```powershell
GET /health
GET /ready
GET /live
GET /metrics
```

Obs.: `GET /metrics` exige `X-System`, `X-API-Key` e scope `metrics:read`.

5. Executar smoke test de:

- `POST /v1/subscriptions`
- `GET /v1/jobs/{job_id}`

## Rollback

Use rollback quando houver:

- erro consistente de bootstrap
- falha massiva em criacao de assinatura
- migration com impacto nao esperado

### Passos

1. Bloquear novas chamadas dos consumidores.
2. Redirecionar trafego para versao anterior.
3. Se necessario, reverter migration:

```powershell
python -m alembic downgrade -1
```

4. Validar `current`:

```powershell
python -m alembic current
```

5. Confirmar integridade funcional minima com `/ready`.

## Pos-deploy

- registrar versao implantada
- registrar horario
- guardar evidencias de smoke test
- abrir incidente se houve rollback
