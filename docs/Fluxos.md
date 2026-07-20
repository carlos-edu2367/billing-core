# Fluxos Operacionais - Billing Core

## Assinatura

1. O sistema chama `POST /v1/subscriptions`.
2. Auth, escopo e idempotência são validados; o worker cria a assinatura e persiste seu espelho local.
3. O consumidor consulta `GET /v1/jobs/{job_id}` e recebe atualizações internas assinadas.

## Checkout

1. O sistema chama `POST /v1/payments` com um item ou mais, callbacks permitidos e `Idempotency-Key`.
2. Auth, escopo, soma de itens, hosts de retorno e idempotência são validados.
3. O request vira o job `create_checkout_worker` e a operação é persistida para recuperação idempotente.
4. O worker cria checkout detached no Asaas com PIX e cartão, persiste `checkout_id`, URL e `externalReference`.
5. O consumidor consulta o job e redireciona o comprador para `checkout_url`.
6. Os retornos de navegador não mudam o estado financeiro.
7. `CHECKOUT_CREATED`, `CHECKOUT_CANCELED`, `CHECKOUT_EXPIRED` e `CHECKOUT_PAID` chegam pelo webhook Asaas, são deduplicados e atualizam o pagamento local.
8. Somente `CHECKOUT_PAID` produz a entrega interna assinada que o produto pode usar para conceder o benefício.

## Falhas e reconciliação

- validação de borda retorna erro HTTP e não enfileira job;
- falhas transitórias tentam novamente e preservam metadados;
- uma operação que precisa de reconciliação consulta o checkout remoto antes de concluir;
- a entrega interna com falha permanece observável para reprocessamento.
