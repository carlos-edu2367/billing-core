# Task 3 — Persistência idempotente de checkout

## Mudanças

- Adicionado `CreateCheckout` em `app/application/use_cases/create_checkout.py`.
- Adicionados seis testes de caso de uso em `tests/test_create_checkout_use_case.py`.
- O caso de uso usa a chave de deduplicação `create_checkout:{system}:{system_payment_id}` e persiste `GatewayOperation` antes da chamada remota.
- O checkout remoto é criado com `PIX`/`CREDIT_CARD`, `DETACHED`, callbacks e itens em camelCase.
- Após a criação remota, persiste `Payment` com o ID/link do checkout, referência `checkout:{system}:{system_payment_id}`, vencimento nulo, tipo `UNDEFINED` e status `PENDING`.
- Falhas de gravação local após a criação remota marcam a operação como `REQUIRES_RECONCILIATION`.

## TDD

- **RED:** `python -m pytest tests/test_create_checkout_use_case.py -q` falhou durante a coleta com `ModuleNotFoundError: No module named 'app.application.use_cases.create_checkout'`, pois o caso de uso ainda não existia.
- **GREEN:** após a implementação mínima, o mesmo comando passou com `5 passed in 1.15s`.

## Testes

- Focado: `python -m pytest tests/test_create_checkout_use_case.py -q` — `5 passed`.
- Suíte completa final: `python -m pytest -q` — `124 passed, 52 warnings in 6.74s`.
- Os 52 avisos são de deprecações de códigos HTTP no Starlette/FastAPI, já presentes fora deste escopo.

## Auto-revisão

- Confirmei o reuso de pagamento local sem chamada ao gateway.
- Confirmei retry para operação `FAILED` sem referência do gateway.
- Rejeitei operação `FAILED` que já possui referência remota, evitando a duplicação de checkouts; essa proteção foi adicionada após revisão independente.
- Confirmei erro de domínio para operação `COMPLETED` sem espelho local.
- Confirmei a transição para `REQUIRES_RECONCILIATION` se a persistência local falhar depois da criação remota.
- Mantive `app/application/use_cases/create_payment.py` e `tests/test_create_payment_use_case.py` intactos: o worker legado ainda os importa. A remoção coordenada fica explicitamente para a Task 4.
