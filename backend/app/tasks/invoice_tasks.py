import logging
from typing import Any, Dict
from sqlalchemy.exc import OperationalError
from app.core.celery_app import celery_app
from app.core.config import settings
from app.database.session import SessionLocal
from app.services.invoice_service import BillingCycleNotFoundError, InvoiceService

logger = logging.getLogger("subscription_billing.tasks.invoice")


@celery_app.task(
    name="app.tasks.invoice_tasks.generate_invoice_for_billing_cycle",
    bind=True,
    queue="invoicing",
    max_retries=settings.CELERY_TASK_MAX_RETRIES,
    default_retry_delay=settings.CELERY_TASK_RETRY_DELAY_SECONDS,
)
def generate_invoice_for_billing_cycle(self, billing_cycle_id: int) -> Dict[str, Any]:
    """
    Celery task that generates an itemized Invoice for a BillingCycle using InvoiceService.

    Safe under duplicate task delivery, Celery retries, and concurrent workers due
    to row-level locking and the unique billing_cycle_id constraint.
    """
    logger.info("Invoice generation task invoked for billing_cycle_id=%s", billing_cycle_id)
    db = SessionLocal()
    try:
        invoice, created = InvoiceService.generate_invoice_for_billing_cycle(
            db,
            billing_cycle_id=billing_cycle_id,
            actor="celery_invoice_worker",
        )
        return {
            "status": "invoice_created" if created else "already_exists",
            "created": created,
            "invoice_id": invoice.id,
            "invoice_number": invoice.invoice_number,
            "billing_cycle_id": invoice.billing_cycle_id,
            "subscription_id": invoice.subscription_id,
            "customer_id": invoice.customer_id,
            "subtotal": str(invoice.subtotal),
            "tax_amount": str(invoice.tax_amount),
            "discount_amount": str(invoice.discount_amount),
            "total_amount": str(invoice.total_amount),
            "currency": invoice.currency,
        }
    except BillingCycleNotFoundError:
        logger.warning("BillingCycle %s not found during invoice generation.", billing_cycle_id)
        return {
            "status": "billing_cycle_not_found",
            "billing_cycle_id": billing_cycle_id,
        }
    except OperationalError as exc:
        db.rollback()
        logger.warning(
            "Transient DB error generating invoice for billing_cycle_id=%s, scheduling retry: %s",
            billing_cycle_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=settings.CELERY_TASK_RETRY_DELAY_SECONDS)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "Unexpected error generating invoice for billing_cycle_id=%s, scheduling retry: %s",
            billing_cycle_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=settings.CELERY_TASK_RETRY_DELAY_SECONDS)
    finally:
        db.close()

