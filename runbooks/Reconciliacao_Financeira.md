# Runbook - Reconciliacao Financeira

## Quando usar

- suspeita de assinatura criada no gateway e nao persistida
- pagamento confirmado no gateway e nao refletido no core
- webhook processado parcialmente
- divergencia entre `Payment` e `Subscription`

## Dados que precisam ser levantados

- `system`
- `system_sub_id`
- `gateway_subscription_id`
- `system_payment_id`
- `provider_payment_id`
- `job_id`
- `event_id` de `webhook_events`

## Consultas recomendadas

### Assinatura

- buscar por `system_subscription_id + from_system`
- buscar por `gateway_subscription_id`

### Pagamento

- buscar por `system_payment_id`
- buscar por `provider_payment_id`
- listar pagamentos por `subscription_id`

### Webhook

- buscar por `event_id`
- validar `processed`

## Analise

1. Confirmar se o provider reconhece a assinatura.
2. Confirmar se o core gravou `Subscription`.
3. Confirmar se existe `Payment` correspondente.
4. Confirmar se o `WebhookEvent` foi marcado como processado.

## Acao manual

- nunca recriar cobranca sem checar `provider_payment_id`
- nunca reenfileirar em massa sem identificar o estado atual
- se necessario, reprocessar o evento de forma controlada usando os identificadores reais

## Resultado esperado

- banco e gateway alinhados
- evidencias do incidente registradas
- follow-up tecnico aberto se a reconciliacao depender de acao manual repetitiva
