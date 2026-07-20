from datetime import datetime, timezone
from uuid import UUID

from app.application.dtos.request.payment import CreatePaymentDTO
from app.application.dtos.request.payment_link import CreatePaymentLinkDTO
from app.application.dtos.request.subscription import CreateSubscriptionDTO
from app.application.dtos.request.subscription_cancel import CancelSubscriptionDTO
from app.application.dtos.request.webhook import WebhookPayload
from app.application.dtos.response.webhook import InternalEventType, SendInternalWebhookPayment, SendInternalWebhookSubscription
from app.application.use_cases.cancel_subscription import CancelSubscription
from app.application.use_cases.create_payment import CreatePayment
from app.application.use_cases.create_payment_link import CreatePaymentLink
from app.infra.interfaces.asaas_provider import AsaasAPIError
from app.application.use_cases.create_subscription import CreateSubscription
from app.application.use_cases.reconcile_payment import ReconcilePayment
from app.domain.entities.internal_webhook_delivery import InternalWebhookDelivery
from app.domain.entities.payment import Payment
from app.domain.entities.subscription import Subscription, SubscriptionStatus
from app.domain.enums.gateway_operation_status import GatewayOperationStatus
from app.domain.errors import DomainError, NotFoundError
from app.domain.enums.gateway_provider import GatewayProvider
from app.domain.enums.payment_status import PaymentStatus
from app.domain.enums.system import System
from app.domain.enums.subscription_type import SubscriptionType
from app.domain.enums.payment_type import PaymentType
from app.infra.db.models.gateway_operation import GatewayOperationModel
from decimal import Decimal
from app.infra.config import settings
from app.infra.db.setup import AsyncSessionLocal
from app.infra.jobs import register_dead_letter, update_job_metadata
from app.infra.interfaces.gateway_provider import GetGatewayInfra
from app.infra.interfaces.internal_webhook import InternalWebhookProvider
from app.infra.interfaces.uow_provider import UowProvider
from app.infra.repo.customer_repo import CustomerRepositoryINFRA
from app.infra.repo.gateway_operation_repo import GatewayOperationRepositoryINFRA
from app.infra.repo.internal_webhook_delivery_repo import InternalWebhookDeliveryRepositoryINFRA
from app.infra.repo.payment_repo import PaymentRepositoryINFRA
from app.infra.repo.subscription_repo import SubscriptionRepositoryINFRA
from app.infra.repo.webhook_event_repo import WebhookEventRepositoryINFRA
from app.application.use_cases.process_webhook import ProcessWebhookService


def _dump_result(result):
    if result is None:
        return None

    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")

    return result


async def _build_internal_delivery(
    result,
    sub_repo: SubscriptionRepositoryINFRA,
    payment_repo: PaymentRepositoryINFRA,
) -> InternalWebhookDelivery | None:
    if result is None or result.subscription_id is None:
        return None

    subscription = await sub_repo.get_by_id(result.subscription_id)
    if not subscription.webhook_link:
        return None

    payment_date = None
    payment_id = result.payment_id
    if result.event == InternalEventType.PAYMENT_RECEIVED:
        if payment_id is None:
            raise DomainError("Evento interno de pagamento recebido sem payment_id.")
        payment = await payment_repo.get_by_id(payment_id)
        if not payment.paid_date:
            raise DomainError("Pagamento confirmado sem paid_date para envio de webhook interno.")
        payment_date = payment.paid_date.date()

    payload = SendInternalWebhookSubscription(
        event=result.event,
        subscription_id=subscription.id,
        system_sub_id=subscription.system_subscription_id,
        subscription_expires_at=subscription.expires_at.date(),
        payment_date=payment_date,
    )
    dedupe_key = f"{result.event.value}:{subscription.id}:{payment_id or 'no-payment'}"
    return InternalWebhookDelivery(
        dedupe_key=dedupe_key,
        event_type=result.event.value,
        target_url=subscription.webhook_link,
        payload=payload.model_dump(mode="json"),
        subscription_id=subscription.id,
        payment_id=payment_id,
    )


async def _build_payment_internal_delivery(payment) -> InternalWebhookDelivery | None:
    if not payment.webhook_link:
        return None

    payload = SendInternalWebhookPayment(
        event=InternalEventType.PAYMENT_STATUS_UPDATED,
        payment_id=payment.id,
        system_payment_id=payment.system_payment_id,
        payment_status=payment.payment_status,
        value=payment.value,
        paid_date=payment.paid_date.date() if payment.paid_date else None,
        checkout_url=payment.checkout_link,
    )
    dedupe_key = f"payment:{payment.id}:{payment.payment_status.value}:{payment.updated_at.isoformat()}"
    return InternalWebhookDelivery(
        dedupe_key=dedupe_key,
        event_type=payload.event.value,
        target_url=payment.webhook_link,
        payload=payload.model_dump(mode="json"),
        subscription_id=None,
        payment_id=payment.id,
    )


async def process_webhook(ctx, payload_dict: dict, gateway_provider_str: str):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]
    release_lock = False

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
        )
        gateway_provider = GatewayProvider[gateway_provider_str.upper()]
        payload = WebhookPayload.model_validate(payload_dict)
        event_id = payload.event_id_for(gateway_provider)
        lock_key = f"billing_core:webhook_lock:{event_id}"

        # Store job_id as lock value so retries of this same job can re-acquire.
        lock_acquired = await ctx["redis"].set(
            lock_key,
            job_id,
            ex=settings.WEBHOOK_PROCESSING_LOCK_TTL_SECONDS,
            nx=True,
        )

        if not lock_acquired:
            current_holder = await ctx["redis"].get(lock_key)
            if isinstance(current_holder, bytes):
                current_holder = current_holder.decode()
            if current_holder == job_id:
                # This is a retry of the same job re-acquiring its own lock.
                lock_acquired = True
            else:
                await update_job_metadata(
                    ctx["redis"],
                    job_id,
                    status="completed",
                    finished_at=datetime.now(timezone.utc),
                )
                return {"status": "duplicate", "result": None}

        internal_delivery_id: UUID | None = None
        async with AsyncSessionLocal() as session:
            payment_repo = PaymentRepositoryINFRA(session)
            sub_repo = SubscriptionRepositoryINFRA(session)
            webhook_event_repo = WebhookEventRepositoryINFRA(session)
            delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
            uow = UowProvider(session)

            service = ProcessWebhookService(
                payment_repo=payment_repo,
                sub_repo=sub_repo,
                uow=uow,
                webhook_event_repo=webhook_event_repo,
            )
            result = await service.execute(gateway_provider, payload)

            if result is not None:
                if result.subscription_id is None and result.payment_id is not None:
                    payment = await payment_repo.get_by_id(result.payment_id)
                    delivery = await _build_payment_internal_delivery(payment)
                else:
                    delivery = await _build_internal_delivery(result, sub_repo, payment_repo)
                if delivery is not None:
                    existing_delivery = await delivery_repo.get_by_dedupe_key(delivery.dedupe_key)
                    if existing_delivery is None:
                        delivery = await delivery_repo.save(delivery)
                        await uow.commit()
                        internal_delivery_id = delivery.id

        if internal_delivery_id is not None:
            await ctx["redis"].enqueue_job(
                "workers:tasks.send_internal_webhook",
                str(internal_delivery_id),
            )

        release_lock = True
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info("Webhook processed", extra={"job_id": job_id, "job_try": job_try})
        return {"status": "success", "result": _dump_result(result), "internal_delivery_id": str(internal_delivery_id) if internal_delivery_id else None}
    except (DomainError, NotFoundError, ValueError) as exc:
        # Terminal failure — no ARQ retry will follow, so we can release the lock.
        release_lock = True
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "process_webhook", job_id)
        ctx["logger"].warning("Webhook rejected", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except Exception as e:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        # Release lock only on the final attempt; retries must be able to re-acquire it
        # (via job_id check) while concurrent workers for the same event are still blocked.
        release_lock = is_final_try
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=e.__class__.__name__,
            error_message=str(e),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "process_webhook", job_id)
        ctx["logger"].error("Webhook processing failed", extra={"job_id": job_id, "job_try": job_try, "error": str(e)})
        raise
    finally:
        if release_lock and "event_id" in locals():
            await ctx["redis"].delete(f"billing_core:webhook_lock:{event_id}")


async def create_subscription_worker(ctx, dto_dict: dict, customer_provider_id: str, system_str: str):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
        )
        dto = CreateSubscriptionDTO.model_validate(dto_dict)

        async with AsyncSessionLocal() as session:
            customer_repo = CustomerRepositoryINFRA(session)
            sub_repo = SubscriptionRepositoryINFRA(session)
            payment_repo = PaymentRepositoryINFRA(session)
            gateway_operation_repo = GatewayOperationRepositoryINFRA(session)
            uow = UowProvider(session)
            get_gateway = GetGatewayInfra()

            customer = await customer_repo.get_by_provider_id(customer_provider_id)
            service = CreateSubscription(
                get_gateway=get_gateway,
                uow=uow,
                repo=sub_repo,
                payment_repo=payment_repo,
                gateway_operation_repo=gateway_operation_repo,
            )
            result = await service.execute(dto, customer)

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info("Subscription created", extra={"job_id": job_id, "job_try": job_try, "system": system_str})
        return {"status": "success", "result": _dump_result(result)}
    except (DomainError, NotFoundError) as exc:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "create_subscription_worker", job_id)
        ctx["logger"].warning("Subscription rejected", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except AsaasAPIError as exc:
        is_client_error = 400 <= exc.status_code < 500
        if is_client_error:
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc),
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            await register_dead_letter(ctx["redis"], "create_subscription_worker", job_id)
            ctx["logger"].error(
                "Subscription creation failed terminally — Asaas returned client error",
                extra={"job_id": job_id, "job_try": job_try, "asaas_status": exc.status_code, "asaas_body": exc.body},
            )
            return {"status": "failed", "error": str(exc)}
        else:
            is_final_try = job_try >= settings.WORKER_MAX_TRIES
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed" if is_final_try else "retrying",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc) if is_final_try else None,
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            if is_final_try:
                await register_dead_letter(ctx["redis"], "create_subscription_worker", job_id)
            ctx["logger"].warning(
                "Subscription creation transient failure — Asaas returned server error, retry scheduled",
                extra={"job_id": job_id, "job_try": job_try, "asaas_status": exc.status_code, "asaas_body": exc.body},
            )
            raise
    except Exception as e:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=e.__class__.__name__,
            error_message=str(e),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "create_subscription_worker", job_id)
        ctx["logger"].error("Subscription creation failed", extra={"job_id": job_id, "job_try": job_try, "error": str(e)})
        raise


async def create_payment_worker(ctx, dto_dict: dict, customer_provider_id: str, system_str: str):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
        )
        dto = CreatePaymentDTO.model_validate(dto_dict | {"customer_provider_id": customer_provider_id})
        internal_delivery_id: UUID | None = None

        async with AsyncSessionLocal() as session:
            customer_repo = CustomerRepositoryINFRA(session)
            payment_repo = PaymentRepositoryINFRA(session)
            gateway_operation_repo = GatewayOperationRepositoryINFRA(session)
            delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
            uow = UowProvider(session)
            get_gateway = GetGatewayInfra()

            customer = await customer_repo.get_by_provider_id(customer_provider_id)
            service = CreatePayment(
                get_gateway=get_gateway,
                uow=uow,
                payment_repo=payment_repo,
                gateway_operation_repo=gateway_operation_repo,
            )
            result = await service.execute(dto, customer)

            await ctx["redis"].enqueue_job(
                "workers:tasks.reconcile_pending_payment_worker",
                str(result.payment_id),
                _defer_by=900,
            )

            if result.payment_status != PaymentStatus.PENDING:
                payment = await payment_repo.get_by_id(result.payment_id)
                delivery = await _build_payment_internal_delivery(payment)
                if delivery is not None:
                    existing_delivery = await delivery_repo.get_by_dedupe_key(delivery.dedupe_key)
                    if existing_delivery is None:
                        delivery = await delivery_repo.save(delivery)
                        await uow.commit()
                        internal_delivery_id = delivery.id

        if internal_delivery_id is not None:
            await ctx["redis"].enqueue_job(
                "workers:tasks.send_internal_webhook",
                str(internal_delivery_id),
            )

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info("Payment created", extra={"job_id": job_id, "job_try": job_try, "system": system_str})
        return {"status": "success", "result": _dump_result(result)}
    except (DomainError, NotFoundError) as exc:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "create_payment_worker", job_id)
        ctx["logger"].warning("Payment rejected", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except AsaasAPIError as exc:
        # Erros 4xx do Asaas são falhas de negócio (payload inválido, cliente
        # inexistente, etc.) — não há ponto em retentar sem intervenção manual.
        # O body completo já foi logado em asaas_provider._handle_error.
        is_client_error = 400 <= exc.status_code < 500
        if is_client_error:
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc),
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            await register_dead_letter(ctx["redis"], "create_payment_worker", job_id)
            ctx["logger"].error(
                "Payment creation failed terminally — Asaas returned client error",
                extra={
                    "job_id": job_id,
                    "job_try": job_try,
                    "asaas_status": exc.status_code,
                    "asaas_body": exc.body,
                    "terminal": is_client_error,
                },
            )
            return {"status": "failed", "error": str(exc)}
        else:
            is_final_try = job_try >= settings.WORKER_MAX_TRIES
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed" if is_final_try else "retrying",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc) if is_final_try else None,
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            if is_final_try:
                await register_dead_letter(ctx["redis"], "create_payment_worker", job_id)
            ctx["logger"].warning(
                "Payment creation transient failure — Asaas returned server error, retry scheduled",
                extra={"job_id": job_id, "job_try": job_try, "asaas_status": exc.status_code, "asaas_body": exc.body},
            )
            raise
    except Exception as exc:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "create_payment_worker", job_id)
        ctx["logger"].error("Payment creation failed", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        raise


async def create_payment_link_worker(ctx, dto_dict: dict, gateway_provider_str: str):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
        )
        dto = CreatePaymentLinkDTO.model_validate(dto_dict)
        gateway_provider = GatewayProvider[gateway_provider_str.upper()]

        async with AsyncSessionLocal() as session:
            payment_repo = PaymentRepositoryINFRA(session)
            gateway_operation_repo = GatewayOperationRepositoryINFRA(session)
            uow = UowProvider(session)
            get_gateway = GetGatewayInfra()

            service = CreatePaymentLink(
                get_gateway=get_gateway,
                uow=uow,
                payment_repo=payment_repo,
                gateway_operation_repo=gateway_operation_repo,
            )
            result = await service.execute(dto, gateway_provider)

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info("Payment link created", extra={"job_id": job_id, "job_try": job_try, "system": dto.system.value})
        return {"status": "success", "result": _dump_result(result)}
    except (DomainError, NotFoundError, ValueError) as exc:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "create_payment_link_worker", job_id)
        ctx["logger"].warning("Payment link rejected", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except AsaasAPIError as exc:
        is_client_error = 400 <= exc.status_code < 500
        if is_client_error:
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc),
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            await register_dead_letter(ctx["redis"], "create_payment_link_worker", job_id)
            ctx["logger"].error(
                "Payment link creation failed terminally - Asaas returned client error",
                extra={
                    "job_id": job_id,
                    "job_try": job_try,
                    "asaas_status": exc.status_code,
                    "asaas_body": exc.body,
                    "terminal": is_client_error,
                },
            )
            return {"status": "failed", "error": str(exc)}
        else:
            is_final_try = job_try >= settings.WORKER_MAX_TRIES
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed" if is_final_try else "retrying",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc) if is_final_try else None,
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            if is_final_try:
                await register_dead_letter(ctx["redis"], "create_payment_link_worker", job_id)
            ctx["logger"].warning(
                "Payment link creation transient failure - Asaas returned server error, retry scheduled",
                extra={"job_id": job_id, "job_try": job_try, "asaas_status": exc.status_code, "asaas_body": exc.body},
            )
            raise
    except Exception as exc:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "create_payment_link_worker", job_id)
        ctx["logger"].error("Payment link creation failed", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        raise


async def reconcile_pending_payment_worker(ctx, payment_id: str):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
            resource_type="payment",
            resource_id=payment_id,
        )
        internal_delivery_id: UUID | None = None

        async with AsyncSessionLocal() as session:
            payment_repo = PaymentRepositoryINFRA(session)
            delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
            uow = UowProvider(session)
            get_gateway = GetGatewayInfra()

            service = ReconcilePayment(
                get_gateway=get_gateway,
                uow=uow,
                payment_repo=payment_repo,
            )
            result = await service.execute(UUID(payment_id))

            if result is not None:
                delivery = await _build_payment_internal_delivery(result)
                if delivery is not None:
                    existing_delivery = await delivery_repo.get_by_dedupe_key(delivery.dedupe_key)
                    if existing_delivery is None:
                        delivery = await delivery_repo.save(delivery)
                        await uow.commit()
                        internal_delivery_id = delivery.id

        if internal_delivery_id is not None:
            await ctx["redis"].enqueue_job(
                "workers:tasks.send_internal_webhook",
                str(internal_delivery_id),
            )

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info("Payment reconciled", extra={"job_id": job_id, "job_try": job_try, "payment_id": payment_id})
        return {"status": "success", "payment_id": payment_id}
    except (DomainError, NotFoundError, ValueError) as exc:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "reconcile_pending_payment_worker", job_id)
        ctx["logger"].warning("Payment reconciliation rejected", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "reconcile_pending_payment_worker", job_id)
        ctx["logger"].error("Payment reconciliation failed", extra={"job_id": job_id, "job_try": job_try, "error": str(exc)})
        raise


async def cancel_subscription_worker(ctx, dto_dict: dict):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
            operation="cancel",
        )
        dto = CancelSubscriptionDTO.model_validate(dto_dict | {"job_id": job_id})
        ctx["logger"].info(
            "Subscription cancellation processing started",
            extra={"job_id": job_id, "job_try": job_try, "subscription_id": str(dto.subscription_id), "system": dto.system.value},
        )

        async with AsyncSessionLocal() as session:
            sub_repo = SubscriptionRepositoryINFRA(session)
            gateway_operation_repo = GatewayOperationRepositoryINFRA(session)
            uow = UowProvider(session)
            get_gateway = GetGatewayInfra()

            service = CancelSubscription(
                get_gateway=get_gateway,
                uow=uow,
                repo=sub_repo,
                gateway_operation_repo=gateway_operation_repo,
            )
            result = await service.execute(dto)

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info(
            "Subscription cancellation completed",
            extra={"job_id": job_id, "job_try": job_try, "subscription_id": str(dto.subscription_id), "system": dto.system.value},
        )
        return {"status": "success", "result": _dump_result(result)}
    except (DomainError, NotFoundError) as exc:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "cancel_subscription_worker", job_id)
        ctx["logger"].warning(
            "Subscription cancellation rejected",
            extra={"job_id": job_id, "job_try": job_try, "error": str(exc)},
        )
        return {"status": "failed", "error": str(exc)}
    except AsaasAPIError as exc:
        is_client_error = 400 <= exc.status_code < 500
        if is_client_error:
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc),
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            await register_dead_letter(ctx["redis"], "cancel_subscription_worker", job_id)
            ctx["logger"].error(
                "Subscription cancellation failed terminally — Asaas returned client error",
                extra={"job_id": job_id, "job_try": job_try, "asaas_status": exc.status_code, "asaas_body": exc.body},
            )
            return {"status": "failed", "error": str(exc)}
        else:
            is_final_try = job_try >= settings.WORKER_MAX_TRIES
            await update_job_metadata(
                ctx["redis"],
                job_id,
                status="failed" if is_final_try else "retrying",
                attempt=job_try,
                finished_at=datetime.now(timezone.utc) if is_final_try else None,
                error_code=f"AsaasAPIError_{exc.status_code}",
                error_message=exc.body[:500],
            )
            if is_final_try:
                await register_dead_letter(ctx["redis"], "cancel_subscription_worker", job_id)
            ctx["logger"].warning(
                "Subscription cancellation transient failure — Asaas returned server error, retry scheduled",
                extra={"job_id": job_id, "job_try": job_try, "asaas_status": exc.status_code, "asaas_body": exc.body},
            )
            raise
    except Exception as exc:
        is_final_try = job_try >= settings.WORKER_MAX_TRIES
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "cancel_subscription_worker", job_id)
            ctx["logger"].error(
                "Subscription cancellation failed after retries",
                extra={"job_id": job_id, "job_try": job_try, "error": str(exc)},
            )
        else:
            ctx["logger"].warning(
                "Subscription cancellation gateway failure, retry scheduled",
                extra={"job_id": job_id, "job_try": job_try, "error": str(exc)},
            )
        raise


async def send_internal_webhook(ctx, delivery_id: str):
    job_id = ctx["job_id"]
    job_try = ctx["job_try"]

    try:
        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="processing",
            attempt=job_try,
            started_at=datetime.now(timezone.utc),
            error_code=None,
            error_message=None,
            resource_type="internal_webhook",
            delivery_id=delivery_id,
        )

        async with AsyncSessionLocal() as session:
            delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
            uow = UowProvider(session)
            internal_webhook = InternalWebhookProvider()

            delivery = await delivery_repo.get_by_id(UUID(delivery_id))
            delivery.register_attempt()
            await delivery_repo.save(delivery)
            await uow.commit()

            await internal_webhook.send(
                url=delivery.target_url,
                payload=delivery.payload,
                webhook_id=str(delivery.id) if delivery.id else delivery.dedupe_key,
                event_type=delivery.event_type,
            )

            delivery.mark_delivered()
            await delivery_repo.save(delivery)
            await uow.commit()

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
        )
        ctx["logger"].info("Internal webhook delivered", extra={"job_id": job_id, "delivery_id": delivery_id, "job_try": job_try})
        return {"status": "success", "delivery_id": delivery_id}
    except (DomainError, NotFoundError, ValueError) as exc:
        async with AsyncSessionLocal() as session:
            delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
            uow = UowProvider(session)
            try:
                delivery = await delivery_repo.get_by_id(UUID(delivery_id))
                delivery.mark_failed(str(exc))
                await delivery_repo.save(delivery)
                await uow.commit()
            except Exception:
                await uow.rollback()

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc),
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        await register_dead_letter(ctx["redis"], "send_internal_webhook", job_id)
        ctx["logger"].warning("Internal webhook rejected", extra={"job_id": job_id, "delivery_id": delivery_id, "error": str(exc)})
        return {"status": "failed", "error": str(exc)}
    except Exception as exc:
        is_final_try = job_try >= settings.INTERNAL_WEBHOOK_MAX_TRIES

        async with AsyncSessionLocal() as session:
            delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
            uow = UowProvider(session)
            try:
                delivery = await delivery_repo.get_by_id(UUID(delivery_id))
                if is_final_try:
                    delivery.mark_failed(str(exc))
                else:
                    delivery.mark_retrying(str(exc))
                await delivery_repo.save(delivery)
                await uow.commit()
            except Exception:
                await uow.rollback()

        await update_job_metadata(
            ctx["redis"],
            job_id,
            status="failed" if is_final_try else "retrying",
            attempt=job_try,
            finished_at=datetime.now(timezone.utc) if is_final_try else None,
            error_code=exc.__class__.__name__,
            error_message=str(exc),
        )
        if is_final_try:
            await register_dead_letter(ctx["redis"], "send_internal_webhook", job_id)
        ctx["logger"].error("Internal webhook delivery failed", extra={"job_id": job_id, "delivery_id": delivery_id, "job_try": job_try, "error": str(exc)})
        raise


async def reconcile_gateway_operations_worker(ctx):
    """Worker periódico que varre gateway_operations em REQUIRES_RECONCILIATION e as resolve."""
    ctx["logger"].info("Starting gateway operations reconciliation...")
    
    # 1. Distributed lock to avoid multi-worker concurrent reconciliation runs
    lock_key = "billing_core:reconcile_worker_lock"
    lock_acquired = await ctx["redis"].set(
        lock_key,
        "locked",
        ex=840,  # 14 minutes (slightly less than the 15-minute cron interval)
        nx=True,
    )
    if not lock_acquired:
        ctx["logger"].info("Reconciliation worker lock is already held. Skipping execution.")
        return {"status": "skipped", "reason": "lock_held"}

    try:
        async with AsyncSessionLocal() as session:
            op_repo = GatewayOperationRepositoryINFRA(session)
            operations = await op_repo.list_by_status(GatewayOperationStatus.REQUIRES_RECONCILIATION)
            op_ids = [op.id for op in operations]

        for op_id in op_ids:
            async with AsyncSessionLocal() as session:
                uow = UowProvider(session)
                op_repo = GatewayOperationRepositoryINFRA(session)
                sub_repo = SubscriptionRepositoryINFRA(session)
                payment_repo = PaymentRepositoryINFRA(session)
                delivery_repo = InternalWebhookDeliveryRepositoryINFRA(session)
                get_gateway = GetGatewayInfra()
                internal_delivery_id: UUID | None = None

                try:
                    async with session.begin():
                        # SELECT FOR UPDATE on GatewayOperation to lock row
                        record = await session.get(GatewayOperationModel, op_id, with_for_update=True)
                        if not record or record.status != GatewayOperationStatus.REQUIRES_RECONCILIATION.value:
                            continue

                        op = record.to_domain()
                        gateway = get_gateway.get(op.provider)

                        if op.operation_name == "create_subscription":
                            status_response = await gateway.verify_status(op.gateway_reference)
                            
                            status_str = status_response.status.upper()
                            if status_response.deleted or status_str in {"INACTIVE", "CANCELED", "CANCELLED", "DELETED"}:
                                local_status = SubscriptionStatus.CANCELED
                            elif status_str == "ACTIVE":
                                local_status = SubscriptionStatus.ACTIVE
                            else:
                                local_status = SubscriptionStatus.PENDING

                            try:
                                sub = await sub_repo.get_by_provider_id_for_update(op.gateway_reference)
                            except NotFoundError:
                                sub = Subscription(
                                    initial_date=op.created_at,
                                    description=op.request_payload.get("description", ""),
                                    system_subscription_id=op.request_payload["system_sub_id"],
                                    gateway_subscription_id=op.gateway_reference,
                                    gateway_provider=op.provider,
                                    status=local_status,
                                    last_paid_date=None,
                                    from_system=System(op.system) if isinstance(op.system, str) else op.system,
                                    subscription_type=SubscriptionType(op.request_payload["subscription_type"]) if isinstance(op.request_payload["subscription_type"], str) else op.request_payload["subscription_type"],
                                    expires_at=datetime.fromisoformat(op.request_payload["expires_at"]) if op.request_payload.get("expires_at") else None,
                                    next_due_date=datetime.fromisoformat(op.request_payload["next_due_date"]) if op.request_payload.get("next_due_date") else None,
                                    value=Decimal(str(op.request_payload["value"])),
                                    webhook_link=op.request_payload.get("webhook_link"),
                                )
                                sub = await sub_repo.save(sub)

                            payments = await gateway.get_subscription_payment(op.gateway_reference)
                            for pay_info in payments:
                                try:
                                    await payment_repo.get_by_provider_id(pay_info.payment_id)
                                except NotFoundError:
                                    billing_type_val = getattr(pay_info, "billing_type", "CREDIT_CARD") or "CREDIT_CARD"
                                    try:
                                        p_type = PaymentType[billing_type_val.upper()]
                                    except KeyError:
                                        p_type = PaymentType.CREDIT_CARD

                                    payment = Payment.create_subscription_payment(
                                        description=f"Pagamento relacionado a assinatura: {sub.description}",
                                        gateway=sub.gateway_provider,
                                        system_payment_id=f"{sub.system_subscription_id}:{pay_info.payment_id}",
                                        provider_payment_id=pay_info.payment_id,
                                        value=pay_info.value,
                                        from_system=sub.from_system,
                                        subscription_id=sub.id,
                                        checkout_link=pay_info.invoice_url,
                                        payment_type=p_type,
                                    )
                                    await payment_repo.save(payment)

                            op.mark_completed(gateway_reference=op.gateway_reference)
                            await op_repo.save(op)
                            ctx["logger"].info(f"Reconciled create_subscription for {op.gateway_reference}")

                        elif op.operation_name == "cancel_subscription":
                            status_response = await gateway.verify_status(op.gateway_reference)
                            if status_response.deleted or status_response.status.lower() in {"inactive", "canceled", "cancelled", "deleted"}:
                                sub_id = op.request_payload["subscription_id"]
                                sub = await sub_repo.get_by_id_for_update(UUID(sub_id))
                                if sub.status != SubscriptionStatus.CANCELED:
                                    sub.cancel(reason="Reconciled cancellation", cancelled_at=datetime.now(timezone.utc))
                                    await sub_repo.save(sub)
                                op.mark_completed(gateway_reference=op.gateway_reference)
                                await op_repo.save(op)
                                ctx["logger"].info(f"Reconciled cancel_subscription for {op.gateway_reference}")
                        elif op.operation_name == "create_checkout":
                            checkout = await gateway.get_checkout(op.gateway_reference)
                            system = System(op.system) if isinstance(op.system, str) else op.system
                            expected_external_reference = f"checkout:{system.value}:{op.request_payload['system_payment_id']}"
                            if checkout.external_reference != expected_external_reference:
                                raise DomainError("Checkout remoto retornou externalReference divergente durante reconciliacao.")

                            checkout_status = checkout.status.upper()
                            payment_statuses = {
                                "ACTIVE": PaymentStatus.PENDING,
                                "PAID": PaymentStatus.PAID,
                                "CANCELED": PaymentStatus.CANCELED,
                                "EXPIRED": PaymentStatus.EXPIRED,
                            }
                            local_status = payment_statuses.get(checkout_status)
                            if local_status is None:
                                raise DomainError(f"Status de checkout remoto desconhecido: {checkout.status}")

                            payment = await payment_repo.get_by_system_ref(
                                op.request_payload["system_payment_id"],
                                system,
                            )
                            if payment is None:
                                payment = Payment.create_standalone_payment(
                                    description=op.request_payload.get("description", ""),
                                    gateway=op.provider,
                                    system_payment_id=op.request_payload["system_payment_id"],
                                    provider_payment_id=checkout.checkout_id,
                                    value=Decimal(str(op.request_payload["value"])),
                                    from_system=system,
                                    checkout_link=checkout.checkout_url,
                                    webhook_link=op.request_payload.get("webhook_link"),
                                    due_date=None,
                                    external_reference=expected_external_reference,
                                )
                                payment.payment_type = PaymentType.UNDEFINED
                                payment.payment_status = local_status
                                payment = await payment_repo.save(payment)
                            elif payment.payment_status != local_status:
                                payment.payment_status = local_status
                                payment.updated_at = datetime.now(timezone.utc)
                                payment = await payment_repo.save(payment)

                            if checkout_status in {"PAID", "CANCELED", "EXPIRED"}:
                                delivery = await _build_payment_internal_delivery(payment)
                                if delivery is not None:
                                    existing_delivery = await delivery_repo.get_by_dedupe_key(delivery.dedupe_key)
                                    if existing_delivery is None:
                                        delivery = await delivery_repo.save(delivery)
                                        internal_delivery_id = delivery.id

                            op.mark_completed(gateway_reference=checkout.checkout_id)
                            await op_repo.save(op)
                            ctx["logger"].info(f"Reconciled create_checkout for {op.gateway_reference}")
                except Exception as e:
                    ctx["logger"].error(f"Failed to reconcile operation {op_id}: {str(e)}")
                else:
                    if internal_delivery_id is not None:
                        await ctx["redis"].enqueue_job(
                            "workers:tasks.send_internal_webhook",
                            str(internal_delivery_id),
                        )
    finally:
        await ctx["redis"].delete(lock_key)
