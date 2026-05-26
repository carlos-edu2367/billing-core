import asyncio
import logging

from arq import Worker, func
from arq.connections import RedisSettings

from app.infra.config import settings
from app.infra.observability import configure_logging, correlation_id_var, metrics
from .tasks import (
    cancel_subscription_worker,
    create_payment_worker,
    create_subscription_worker,
    process_webhook,
    reconcile_pending_payment_worker,
    send_internal_webhook,
)


configure_logging(settings.LOG_LEVEL)
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
                create_payment_worker,
                name="workers:tasks.create_payment_worker",
                keep_result=settings.WORKER_KEEP_RESULT_SECONDS,
                timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                max_tries=settings.WORKER_MAX_TRIES,
            ),
            func(
                reconcile_pending_payment_worker,
                name="workers:tasks.reconcile_pending_payment_worker",
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
