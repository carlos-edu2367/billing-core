import asyncio
import logging

from arq import Worker, func, cron
from arq.connections import RedisSettings

from app.infra.config import settings
from app.infra.observability import configure_logging, correlation_id_var, metrics
from .tasks import (
    cancel_subscription_worker,
    create_checkout_worker,
    create_subscription_worker,
    process_gateway_notification,
    process_webhook,
    send_internal_webhook,
    reconcile_gateway_operations_worker,
    sync_pending_checkouts_worker,
)


configure_logging(settings.LOG_LEVEL)
# Mesma validacao que a API faz no boot: sem isso o worker sobe com credencial
# de gateway vazia e so falha na hora de cobrar um cliente real.
settings.validate_runtime()
logger = logging.getLogger(__name__)


async def startup(ctx):
    ctx['config'] = settings
    ctx['logger'] = logger

async def shutdown(ctx):
    correlation_id_var.set(None)
    pass


async def on_job_start(ctx):
    correlation_id_var.set(ctx["job_id"])
    metrics.increment("worker_jobs_started_total")
    logger.info(
        "Worker job started",
        extra={
            "job_id": ctx["job_id"],
            "job_try": ctx["job_try"],
        },
    )


async def on_job_end(ctx):
    logger.info(
        "Worker job finished",
        extra={
            "job_id": ctx["job_id"],
            "job_try": ctx["job_try"],
        },
    )


async def after_job_end(ctx):
    correlation_id_var.set(None)

def get_worker() -> Worker:
    return Worker(
        functions=[
            func(
                process_webhook,
                name="workers:tasks.process_webhook",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                process_gateway_notification,
                name="workers:tasks.process_gateway_notification",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                create_subscription_worker,
                name="workers:tasks.create_subscription_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                cancel_subscription_worker,
                name="workers:tasks.cancel_subscription_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                create_checkout_worker,
                name="workers:tasks.create_checkout_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                send_internal_webhook,
                name="workers:tasks.send_internal_webhook",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.INTERNAL_WEBHOOK_MAX_TRIES,
            ),
            func(
                reconcile_gateway_operations_worker,
                name="workers:tasks.reconcile_gateway_operations_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                sync_pending_checkouts_worker,
                name="workers:tasks.sync_pending_checkouts_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=1,
            ),
        ],
        cron_jobs=[
            cron(
                reconcile_gateway_operations_worker,
                name="workers:tasks.reconcile_gateway_operations_worker",
                minute={0, 15, 30, 45},
            ),
            cron(
                sync_pending_checkouts_worker,
                name="workers:tasks.sync_pending_checkouts_worker",
                minute=set(range(0, 60, 5)),
            ),
        ],
        redis_settings=RedisSettings.from_dsn(settings.REDIS_URL),
        on_startup=startup,
        on_shutdown=shutdown,
        on_job_start=on_job_start,
        on_job_end=on_job_end,
        after_job_end=after_job_end,
        max_jobs=settings.WORKER_MAX_JOBS,
    )

async def run_worker() -> None:
    worker = get_worker()
    await worker.async_run()


if __name__ == '__main__':
    asyncio.run(run_worker())
