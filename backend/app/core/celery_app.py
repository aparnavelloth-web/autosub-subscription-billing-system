from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "subscription_billing",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.billing_tasks",
        "app.tasks.invoice_tasks",
    ],
)

celery_app.conf.update(
    # Serialization & Timezone
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.CELERY_TIMEZONE,
    enable_utc=True,

    # Reliability, Acknowledgment & Concurrency Safeguards
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,

    # Queues & Routing
    task_default_queue="default",
    task_routes={
        "app.tasks.billing_tasks.*": {"queue": "billing"},
        "app.tasks.invoice_tasks.*": {"queue": "invoicing"},
    },

    # Celery Beat Periodic Schedule
    beat_schedule={
        "process-due-billing-cycles": {
            "task": "app.tasks.billing_tasks.process_due_subscriptions_task",
            "schedule": float(settings.BILLING_SCHEDULER_INTERVAL_SECONDS),
            "options": {"queue": "billing"},
        },
    },
)

