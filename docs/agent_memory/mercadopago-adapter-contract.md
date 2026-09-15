# Adapter Mercado Pago — decisões não óbvias

Implementado em 2026-09-14/15 conforme `docs/superpowers/plans/2026-09-14-mercadopago-gateway-padrao.md`.
Ver também [[food-billing-subscription-contract]] para o contrato de webhook interno que este adapter alimenta.

## O adapter fala o vocabulário do Asaas de propósito

`MercadoPagoProvider` traduz tudo para os mesmos status que o `AsaasProvider` produz (`ACTIVE`,
`PENDING`, `RECEIVED`, `CONFIRMED`, `CHECKOUT_PAID`...). Isso é intencional: `process_webhook`,
`reconcile_gateway_operations_worker` e `CancelSubscription` comparam essas strings diretamente.
Adaptar o vocabulário no gateway novo evitou reescrever esses três lugares. O acoplamento ao
vocabulário do Asaas continua existindo — só foi movido para dentro do adapter.

## Pagamento provisório da assinatura

O Mercado Pago não cobra no momento da criação da assinatura: um `preapproval` sem cartão
tokenizado nasce `status: "pending"` e só gera fatura depois que o pagador autoriza pelo
`init_point`. Como `CreateSubscription` (`app/application/use_cases/create_subscription.py`)
exige um `SubscriptionPaymentResponse` para responder, `MercadoPagoProvider.get_subscription_payment`
devolve um pagamento provisório com `payment_id == subscription_id` (o id do próprio preapproval)
quando não há fatura ainda.

`ProcessWebhookService._find_subscription_payment` (`app/application/use_cases/process_webhook.py`)
procura primeiro pelo `provider_payment_id` real; se não achar, procura um pagamento provisório
`PENDING` com `provider_payment_id == subscription.gateway_subscription_id` e pertencente à mesma
assinatura, e o revincula trocando o `provider_payment_id` pelo id da fatura real. Isso só existe
porque o Asaas nunca produz esse cenário (a cobrança já nasce junto com a assinatura) — não gera
colisão entre os dois gateways.

## `source_event_id` por recurso, não por notificação

A notificação do Mercado Pago não tem um id de evento estável reaproveitável como o `id` do Asaas.
Os mappers (`app/infra/interfaces/mercadopago_mappers.py`) constroem `source_event_id` como
`<recurso>:<id>:<status>` (`payment:123456:approved`, `authorized_payment:7001:approved`,
`preapproval:2c93:cancelled`). Isso é deliberado: uma nova notificação para o mesmo recurso no
mesmo status é deduplicada por `WebhookEvent.event_id`; uma mudança de status gera um id diferente
e é processada de novo.

## Por que existe uma rota de webhook separada

O Mercado Pago não pode reusar `/v1/webhooks/asaas`: autentica por HMAC no header `x-signature`
(manifest `id:<data.id>;request-id:<x-request-id>;ts:<ts>;`), não por token fixo, e o corpo só
traz `data.id` — nunca o estado do recurso. Por isso existe `POST /v1/webhooks/mercadopago`
(`app/web/routes/webhooks.py`) com sua própria dependency (`validate_mercadopago_webhook`) e um
job próprio, `process_gateway_notification`, que chama `gateway.resolve_webhook()` para buscar o
recurso na API antes de entrar no `process_webhook` normal. `InterfaceGateway.resolve_webhook()`
tem uma implementação padrão que só chama `normalize_webhook()` — o Asaas nunca precisou
sobrescrevê-la.

## O cron de sincronização existe só por causa da expiração do Checkout Pro

O Mercado Pago não notifica quando uma preferência do Checkout Pro expira (não existe tópico de
webhook para isso). A renovação de checkout expirado (`Payment.renew_checkout`, ver commit
`0882a17`) depende do status local `EXPIRED` já estar aplicado. Por isso
`sync_pending_checkouts_worker` (cron a cada 5 minutos) varre checkouts `PENDING` do gateway
Mercado Pago e aplica `PAID`/`EXPIRED`/`CANCELED` via `SyncCheckoutStatus`, que chama
`get_checkout()` — que por sua vez deriva o status comparando pagamentos encontrados por
`external_reference` com a janela de vigência da preferência (`resolve_checkout_status` em
`mercadopago_mappers.py`, com folga de `MERCADOPAGO_CHECKOUT_EXPIRY_GRACE_SECONDS` para não
expirar um checkout com pagamento ainda em análise).

## Pontos da doc do Mercado Pago não confirmados em sandbox real

A Task 0 do plano de implementação previa um spike no sandbox do Mercado Pago via MCP para
confirmar 7 pontos antes de implementar; o usuário optou por pular o spike e seguir só com a
documentação pública. **Estes pontos continuam não verificados contra uma chamada real** e devem
ser confirmados manualmente antes da homologação (Task 12, Passo 3):

- (a) se a resposta do `POST /preapproval` realmente traz `init_point`;
- (b) os nomes exatos dos campos de `GET /authorized_payments/{id}` (`preapproval_id`,
  `payment.id`, `payment.status`, `debit_date`, `transaction_amount`);
- (c) se `GET /authorized_payments/search?preapproval_id=` existe como documentado;
- (d) se o pagamento de uma fatura de assinatura expõe
  `point_of_interaction.transaction_data.subscription_id` (usado por
  `subscription_id_from_payment` em `mercadopago_mappers.py` para refund/chargeback recebidos
  pelo tópico `payment`, fora do fluxo normal de `subscription_authorized_payment`);
- (e) se `date_of_expiration` na preferência realmente limita o Pix e se o `payment_type_id` do
  Pix é `bank_transfer`;
- (f) se `PUT /preapproval/{id}` aceita `{"status": "cancelled"}` (grafia com dois `l`) — o código
  usa essa grafia;
- (g) se o pagador precisa necessariamente estar logado com a conta cujo e-mail é o
  `payer_email` enviado na criação do `preapproval`.

Se qualquer um divergir, os pontos de ajuste são: `mercadopago_provider.py` (métodos de
assinatura), `mercadopago_mappers.py` (`authorized_payment_to_webhook`,
`subscription_id_from_payment`) e os testes correspondentes em `tests/test_mercadopago_provider_subscription.py`
e `tests/test_mercadopago_resolve_webhook.py`.

## Bugs encontrados durante a implementação, fora de escopo

- `reconcile_gateway_operations_worker` (ramo `create_subscription`, `app/workers/tasks.py`) usa
  `try/except NotFoundError` em torno de `payment_repo.get_by_provider_id`, mas esse método
  devolve `None` quando não encontra — nunca levanta `NotFoundError`. Na prática, o laço que
  deveria criar pagamentos ausentes durante a reconciliação nunca executa. Afeta os dois gateways
  igualmente; não foi corrigido aqui porque está fora do escopo desta migração. Sinalizado como
  tarefa separada (`task_aec1d897` nesta sessão).
- `ReconcilePayment` (`app/application/use_cases/reconcile_payment.py`) não tem nenhum caller;
  só a função livre `apply_gateway_payment_status` do mesmo módulo é usada.
