from datetime import datetime, timezone
from uuid import UUID

from app.application.dtos.request.subscription import CreateSubscriptionDTO
from app.application.dtos.request.webhook import WebhookPayload
from app.application.dtos.response.webhook import InternalEventType, SendInternalWebhookSubscription
from app.application.use_cases.create_subscription import CreateSubscription
from app.domain.entities.internal_webhook_delivery import InternalWebhookDelivery
from app.domain.errors import DomainError, NotFoundError
from app.domain.enums.gateway_provider import GatewayProvider
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

            await internal_webhook.send(url=delivery.target_url, payload=delivery.payload)

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
