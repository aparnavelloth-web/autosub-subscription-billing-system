import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.exc import OperationalError
from app.core.celery_app import celery_app
from app.core.config import settings
from app.database.session import SessionLocal
from app.services.billing_cycle_service import BillingCycleService

logger = logging.getLogger("subscription_billing.tasks.billing")


def _parse_iso_datetime(iso_value: Optional[str]) -> datetime:
    if not iso_value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(iso_value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@celery_app.task(
    name="app.tasks.billing_tasks.process_due_subscriptions_task",
    bind=True,
    queue="billing",
    max_retries=settings.CELERY_TASK_MAX_RETRIES,
    default_retry_delay=settings.CELERY_TASK_RETRY_DELAY_SECONDS,
)
def process_due_subscriptions_task(
    self,
    as_of_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Periodic Celery Beat task that scans for due active subscriptions,
    creates idempotent BillingCycle records, finalizes end-of-cycle cancellations,
    and enqueues invoice generation tasks.
    """
    cutoff = _parse_iso_datetime(as_of_iso)
    logger.info("Starting billing cycle sweep as_of=%s", cutoff.isoformat())
    try:
        summary = BillingCycleService.run_billing_sweep(
            session_factory=SessionLocal,
            as_of=cutoff,
            actor="celery_beat_scheduler",
        )
        logger.info(
            "Completed billing sweep: due=%s, created=%s, cancelled=%s, errors=%s",
            summary["due_found"],
            summary["cycles_created"],
            summary["cancelled_at_period_end"],
            summary["errors"],
        )
        return summary
    except OperationalError as exc:
        logger.warning("Transient DB failure during billing sweep, scheduling retry: %s", exc)
        raise self.retry(exc=exc, countdown=settings.CELERY_TASK_RETRY_DELAY_SECONDS)
    except Exception as exc:
        logger.exception("Unexpected error during billing sweep, scheduling retry: %s", exc)
        raise self.retry(exc=exc, countdown=settings.CELERY_TASK_RETRY_DELAY_SECONDS)


@celery_app.task(
    name="app.tasks.billing_tasks.process_single_subscription_billing_task",
    bind=True,
    queue="billing",
    max_retries=settings.CELERY_TASK_MAX_RETRIES,
    default_retry_delay=settings.CELERY_TASK_RETRY_DELAY_SECONDS,
)
def process_single_subscription_billing_task(
    self,
    subscription_id: int,
    as_of_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Processes billing cycle renewal for a single subscription ID with row locking,
    idempotency, and Celery retry protection.
    """
    cutoff = _parse_iso_datetime(as_of_iso)
    db = SessionLocal()
    try:
        return BillingCycleService.process_due_subscription(
            db=db,
            subscription_id=subscription_id,
            as_of=cutoff,
            actor="celery_worker",
        )
    except OperationalError as exc:
        db.rollback()
        logger.warning(
            "Transient DB error processing subscription_id=%s, retrying: %s",
            subscription_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=settings.CELERY_TASK_RETRY_DELAY_SECONDS)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Unexpected error processing subscription_id=%s, retrying: %s",
            subscription_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=settings.CELERY_TASK_RETRY_DELAY_SECONDS)
    finally:
        db.close()

