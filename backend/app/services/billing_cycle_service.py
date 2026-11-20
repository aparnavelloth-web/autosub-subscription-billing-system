import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog
from app.models.billing_cycle import BillingCycle
from app.models.enums import BillingCycleStatus, SubscriptionStatus
from app.models.subscription import Subscription
from app.services.billing_dates import calculate_period_end, ensure_utc
from app.services.subscription_service import _serialize_subscription

logger = logging.getLogger("subscription_billing.services.billing_cycle")


def _default_invoice_enqueue(billing_cycle_id: int) -> Any:
    from app.tasks.invoice_tasks import generate_invoice_for_billing_cycle

    return generate_invoice_for_billing_cycle.delay(billing_cycle_id)


def _resolve_billing_anchor_day(sub: Subscription) -> Optional[int]:
    """
    Determines the original calendar day-of-month anchor for month-end alignment,
    respecting trial conversion dates and pause/resume date shifts.
    """
    if sub.resumed_at is not None:
        return None
    if sub.trial_end is not None:
        return ensure_utc(sub.trial_end).day
    return ensure_utc(sub.start_date).day


def _serialize_billing_cycle(cycle: BillingCycle) -> Dict[str, Any]:
    return {
        "id": cycle.id,
        "subscription_id": cycle.subscription_id,
        "cycle_number": cycle.cycle_number,
        "period_start": cycle.period_start.isoformat() if cycle.period_start else None,
        "period_end": cycle.period_end.isoformat() if cycle.period_end else None,
        "billing_date": cycle.billing_date.isoformat() if cycle.billing_date else None,
        "status": cycle.status.value if cycle.status else None,
        "amount": str(cycle.amount),
        "currency": cycle.currency,
    }


class BillingCycleService:
    """
    Idempotent Billing Cycle Scheduler service responsible for detecting due active
    subscriptions, finalizing end-of-cycle cancellations, creating BillingCycle records
    inside locked transactions, advancing renewal boundaries, and handing off cycles
    to the invoice generation queue.
    """

    @staticmethod
    def find_due_subscription_ids(
        db: Session,
        as_of: Optional[datetime] = None,
    ) -> List[int]:
        """
        Identifies IDs of active, non-paused subscriptions whose current_period_end
        is due on or before `as_of`. Excludes trial, past_due, cancelled, and paused
        subscriptions.
        """
        cutoff = ensure_utc(as_of) if as_of is not None else datetime.now(timezone.utc)
        rows = (
            db.query(Subscription.id)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.is_paused.is_(False),
                Subscription.current_period_end <= cutoff,
            )
            .order_by(Subscription.current_period_end.asc(), Subscription.id.asc())
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def create_billing_cycle_idempotent(
        db: Session,
        subscription_id: int,
        cycle_number: int,
        period_start: datetime,
        period_end: datetime,
        billing_date: datetime,
        amount: Decimal,
        currency: str,
        actor: str = "celery_beat_scheduler",
        customer_id: Optional[int] = None,
    ) -> Tuple[BillingCycle, bool]:
        """
        Creates a BillingCycle idempotently guarded by the unique database constraint
        uq_billing_cycles_subscription_cycle (subscription_id, cycle_number).
        Returns (BillingCycle, created_flag).
        """
        existing = (
            db.query(BillingCycle)
            .filter(
                BillingCycle.subscription_id == subscription_id,
                BillingCycle.cycle_number == cycle_number,
            )
            .first()
        )
        if existing is not None:
            return existing, False

        cycle = BillingCycle(
            subscription_id=subscription_id,
            cycle_number=cycle_number,
            period_start=ensure_utc(period_start),
            period_end=ensure_utc(period_end),
            billing_date=ensure_utc(billing_date),
            status=BillingCycleStatus.PENDING,
            amount=amount,
            currency=currency,
        )
        db.add(cycle)
        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="billing_cycle",
                    entity_id=cycle.id,
                    action="billing_cycle.created",
                    actor=actor,
                    customer_id=customer_id,
                    old_values=None,
                    new_values=_serialize_billing_cycle(cycle),
                    description=f"Scheduled billing cycle #{cycle_number} for subscription_id={subscription_id}.",
                )
            )
            return cycle, True
        except IntegrityError:
            db.rollback()
            existing_after_conflict = (
                db.query(BillingCycle)
                .filter(
                    BillingCycle.subscription_id == subscription_id,
                    BillingCycle.cycle_number == cycle_number,
                )
                .one()
            )
            return existing_after_conflict, False

    @staticmethod
    def process_due_subscription(
        db: Session,
        subscription_id: int,
        as_of: Optional[datetime] = None,
        actor: str = "celery_beat_scheduler",
        invoice_enqueue_fn: Optional[Callable[[int], Any]] = None,
    ) -> Dict[str, Any]:
        """
        Safely processes a single subscription inside an isolated transaction using
        PostgreSQL SELECT ... FOR UPDATE row locking.
        """
        now = ensure_utc(as_of) if as_of is not None else datetime.now(timezone.utc)
        enqueue = invoice_enqueue_fn or _default_invoice_enqueue

        sub = (
            db.query(Subscription)
            .filter(Subscription.id == subscription_id)
            .with_for_update()
            .first()
        )
        if sub is None:
            return {"status": "not_found", "subscription_id": subscription_id}

        # Re-verify eligibility inside the row lock
        if sub.status != SubscriptionStatus.ACTIVE:
            return {
                "status": "skipped_ineligible_status",
                "subscription_id": sub.id,
                "subscription_status": sub.status.value,
            }
        if sub.is_paused:
            return {"status": "skipped_paused", "subscription_id": sub.id}

        due_boundary = ensure_utc(sub.current_period_end)
        if due_boundary > now:
            return {"status": "skipped_not_due", "subscription_id": sub.id}

        # Respect end-of-billing-cycle cancellation before generating any renewal cycle
        if sub.cancel_at_period_end:
            old_values = _serialize_subscription(sub)
            sub.status = SubscriptionStatus.CANCELLED
            sub.cancelled_at = now
            sub.cancel_at_period_end = False
            db.flush()
            db.add(
                AuditLog(
                    entity_type="subscription",
                    entity_id=sub.id,
                    action="subscription.cancelled",
                    actor=actor,
                    customer_id=sub.customer_id,
                    old_values=old_values,
                    new_values=_serialize_subscription(sub),
                    description=f"Finalized end-of-cycle cancellation for '{sub.subscription_code}'.",
                )
            )
            db.commit()
            return {"status": "cancelled_at_period_end", "subscription_id": sub.id}

        # Compute next calendar-accurate period boundaries
        new_period_start = due_boundary
        anchor_day = _resolve_billing_anchor_day(sub)
        new_period_end = calculate_period_end(
            new_period_start,
            sub.billing_interval,
            anchor_day=anchor_day,
        )

        # Idempotency check 1: Avoid duplicating an identical period window
        existing_period = (
            db.query(BillingCycle)
            .filter(
                BillingCycle.subscription_id == sub.id,
                BillingCycle.period_start == new_period_start,
                BillingCycle.period_end == new_period_end,
            )
            .first()
        )
        if existing_period is not None:
            return {
                "status": "skipped_duplicate_period",
                "subscription_id": sub.id,
                "billing_cycle_id": existing_period.id,
            }

        max_cycle_num = (
            db.query(func.coalesce(func.max(BillingCycle.cycle_number), 0))
            .filter(BillingCycle.subscription_id == sub.id)
            .scalar()
        )
        next_cycle_number = int(max_cycle_num) + 1
        cycle_amount = sub.unit_price * sub.quantity

        cycle, created = BillingCycleService.create_billing_cycle_idempotent(
            db=db,
            subscription_id=sub.id,
            cycle_number=next_cycle_number,
            period_start=new_period_start,
            period_end=new_period_end,
            billing_date=due_boundary,
            amount=cycle_amount,
            currency=sub.currency,
            actor=actor,
            customer_id=sub.customer_id,
        )
        if not created:
            return {
                "status": "skipped_duplicate_cycle",
                "subscription_id": sub.id,
                "billing_cycle_id": cycle.id,
            }

        # Advance subscription period atomically in the same transaction
        sub_old = _serialize_subscription(sub)
        sub.current_period_start = new_period_start
        sub.current_period_end = new_period_end
        db.flush()
        db.add(
            AuditLog(
                entity_type="subscription",
                entity_id=sub.id,
                action="subscription.period_renewed",
                actor=actor,
                customer_id=sub.customer_id,
                old_values=sub_old,
                new_values=_serialize_subscription(sub),
                description=(
                    f"Advanced subscription '{sub.subscription_code}' to cycle #{next_cycle_number} "
                    f"({new_period_start.date().isoformat()} to {new_period_end.date().isoformat()})."
                ),
            )
        )
        db.commit()
        db.refresh(cycle)

        # Handoff to invoice generation task AFTER database commit
        invoice_enqueued = False
        dispatch_error: Optional[str] = None
        try:
            enqueue(cycle.id)
            invoice_enqueued = True
        except Exception as exc:
            logger.warning(
                "Committed billing_cycle_id=%s but failed to enqueue invoice task: %s",
                cycle.id,
                exc,
            )
            dispatch_error = str(exc)

        return {
            "status": "cycle_created",
            "subscription_id": sub.id,
            "billing_cycle_id": cycle.id,
            "cycle_number": cycle.cycle_number,
            "period_start": cycle.period_start.isoformat(),
            "period_end": cycle.period_end.isoformat(),
            "amount": str(cycle.amount),
            "currency": cycle.currency,
            "invoice_task_enqueued": invoice_enqueued,
            "dispatch_error": dispatch_error,
        }

    @staticmethod
    def run_billing_sweep(
        session_factory: Callable[[], Session],
        as_of: Optional[datetime] = None,
        actor: str = "celery_beat_scheduler",
        invoice_enqueue_fn: Optional[Callable[[int], Any]] = None,
    ) -> Dict[str, Any]:
        """
        Executes a full billing sweep across all due active subscriptions.
        Each subscription is processed inside its own isolated database transaction.
        """
        cutoff = ensure_utc(as_of) if as_of is not None else datetime.now(timezone.utc)

        read_db = session_factory()
        try:
            due_ids = BillingCycleService.find_due_subscription_ids(read_db, as_of=cutoff)
        finally:
            read_db.close()

        summary: Dict[str, Any] = {
            "as_of": cutoff.isoformat(),
            "due_found": len(due_ids),
            "cycles_created": 0,
            "cancelled_at_period_end": 0,
            "skipped": 0,
            "errors": 0,
            "results": [],
        }

        for sub_id in due_ids:
            sub_db = session_factory()
            try:
                outcome = BillingCycleService.process_due_subscription(
                    db=sub_db,
                    subscription_id=sub_id,
                    as_of=cutoff,
                    actor=actor,
                    invoice_enqueue_fn=invoice_enqueue_fn,
                )
                summary["results"].append(outcome)
                status_val = outcome.get("status")
                if status_val == "cycle_created":
                    summary["cycles_created"] += 1
                elif status_val == "cancelled_at_period_end":
                    summary["cancelled_at_period_end"] += 1
                else:
                    summary["skipped"] += 1
            except Exception as exc:
                sub_db.rollback()
                logger.exception("Error processing subscription_id=%s during billing sweep: %s", sub_id, exc)
                summary["errors"] += 1
                summary["results"].append(
                    {"status": "error", "subscription_id": sub_id, "error": str(exc)}
                )
            finally:
                sub_db.close()

        return summary

