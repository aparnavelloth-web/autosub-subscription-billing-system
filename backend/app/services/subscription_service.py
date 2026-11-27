import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.enums import InvoiceItemType, SubscriptionStatus
from app.models.invoice import Invoice
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.schemas.invoice import InvoiceItemCreate
from app.schemas.subscription import (
    ProrationPreviewRequest,
    ProrationPreviewResponse,
    SubscriptionCancelRequest,
    SubscriptionCreate,
    SubscriptionHistoryEvent,
    SubscriptionPauseRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionResumeRequest,
)
from app.services.billing_dates import calculate_period_end, ensure_utc
from app.services.customer_service import CustomerService
from app.services.invoice_service import InvoiceService
from app.services.plan_service import PlanService


class SubscriptionNotFoundError(Exception):
    """Raised when a requested subscription ID does not exist."""


class DuplicateSubscriptionCodeError(Exception):
    """Raised when a subscription_code conflicts with an existing subscription."""


class InvalidSubscriptionStateError(Exception):
    """Raised when a requested subscription state transition or operation is invalid."""


class SubscriptionPauseConflictError(Exception):
    """Raised when pause/resume conflicts with the subscription's current pause state."""


# Validated Subscription State Machine transitions
ALLOWED_SUBSCRIPTION_TRANSITIONS: Dict[SubscriptionStatus, Set[SubscriptionStatus]] = {
    SubscriptionStatus.TRIAL: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED},
    SubscriptionStatus.ACTIVE: {SubscriptionStatus.PAST_DUE, SubscriptionStatus.CANCELLED},
    SubscriptionStatus.PAST_DUE: {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED},
    SubscriptionStatus.CANCELLED: set(),
}


def _generate_subscription_code() -> str:
    return f"SUB-{uuid.uuid4().hex[:8].upper()}"


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _serialize_subscription(sub: Subscription) -> Dict[str, Any]:
    return {
        "id": sub.id,
        "subscription_code": sub.subscription_code,
        "customer_id": sub.customer_id,
        "plan_id": sub.plan_id,
        "status": sub.status.value if sub.status else None,
        "quantity": sub.quantity,
        "unit_price": str(sub.unit_price) if sub.unit_price is not None else None,
        "currency": sub.currency,
        "billing_interval": sub.billing_interval.value if sub.billing_interval else None,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "trial_start": sub.trial_start.isoformat() if sub.trial_start else None,
        "trial_end": sub.trial_end.isoformat() if sub.trial_end else None,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
        "is_paused": sub.is_paused,
        "paused_at": sub.paused_at.isoformat() if sub.paused_at else None,
        "resumed_at": sub.resumed_at.isoformat() if sub.resumed_at else None,
        "paused_remaining_seconds": sub.paused_remaining_seconds,
        "pause_reason": sub.pause_reason,
    }


class SubscriptionService:
    """
    Domain business service governing subscription lifecycle, state transitions,
    plan upgrades/downgrades, pause/resume, cancellations, and historical pricing snapshots.
    """

    @staticmethod
    def _get_subscription_for_update(db: Session, subscription_id: int) -> Subscription:
        """
        Acquires a PostgreSQL row-level exclusive lock (SELECT ... FOR UPDATE)
        to guarantee concurrency safety across state mutations.
        """
        subscription = (
            db.query(Subscription)
            .filter(Subscription.id == subscription_id)
            .with_for_update()
            .first()
        )
        if subscription is None:
            raise SubscriptionNotFoundError(f"Subscription with id {subscription_id} not found.")
        return subscription

    @staticmethod
    def create_subscription(
        db: Session,
        payload: SubscriptionCreate,
        actor: str = "api",
    ) -> Subscription:
        customer = CustomerService.ensure_customer_active(db, payload.customer_id)
        plan = PlanService.ensure_plan_available_for_subscription(db, payload.plan_id)

        sub_code = payload.subscription_code or _generate_subscription_code()
        existing_code = db.query(Subscription).filter(Subscription.subscription_code == sub_code).first()
        if existing_code is not None:
            raise DuplicateSubscriptionCodeError(f"Subscription code '{sub_code}' already exists.")

        start_dt = payload.start_date or datetime.now(timezone.utc)

        if payload.status in (SubscriptionStatus.CANCELLED, SubscriptionStatus.PAST_DUE):
            raise InvalidSubscriptionStateError(
                f"Cannot create a new subscription directly in '{payload.status.value}' status."
            )

        use_trial = (
            payload.status == SubscriptionStatus.TRIAL
            or (payload.status is None and plan.trial_days > 0)
        )

        if use_trial:
            trial_days = plan.trial_days if plan.trial_days > 0 else 14
            status_val = SubscriptionStatus.TRIAL
            trial_start = payload.trial_start or start_dt
            trial_end = payload.trial_end or (trial_start + timedelta(days=trial_days))
            period_start = payload.current_period_start or trial_start
            period_end = payload.current_period_end or trial_end
        else:
            status_val = SubscriptionStatus.ACTIVE
            trial_start = None
            trial_end = None
            period_start = payload.current_period_start or start_dt
            period_end = payload.current_period_end or calculate_period_end(period_start, plan.billing_interval)

        subscription = Subscription(
            subscription_code=sub_code,
            customer_id=customer.id,
            plan_id=plan.id,
            status=status_val,
            quantity=payload.quantity,
            start_date=start_dt,
            current_period_start=period_start,
            current_period_end=period_end,
            trial_start=trial_start,
            trial_end=trial_end,
            cancel_at_period_end=payload.cancel_at_period_end,
            is_paused=False,
        )
        subscription.apply_plan_snapshot(plan)
        db.add(subscription)

        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="subscription",
                    entity_id=subscription.id,
                    action="subscription.created",
                    actor=actor,
                    customer_id=customer.id,
                    old_values=None,
                    new_values=_serialize_subscription(subscription),
                    description=f"Created subscription '{subscription.subscription_code}' on plan '{plan.code}'.",
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicateSubscriptionCodeError(f"Subscription code '{sub_code}' already exists.") from exc

        db.refresh(subscription)
        return subscription

    @staticmethod
    def list_subscriptions(
        db: Session,
        page: int = 1,
        size: int = 20,
        customer_id: Optional[int] = None,
        plan_id: Optional[int] = None,
        status: Optional[SubscriptionStatus] = None,
    ) -> Tuple[List[Subscription], int]:
        query = db.query(Subscription)
        if customer_id is not None:
            query = query.filter(Subscription.customer_id == customer_id)
        if plan_id is not None:
            query = query.filter(Subscription.plan_id == plan_id)
        if status is not None:
            query = query.filter(Subscription.status == status)

        total = query.count()
        items = (
            query.order_by(Subscription.id.desc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
        return items, total

    @staticmethod
    def list_billable_subscriptions(
        db: Session,
        as_of: Optional[datetime] = None,
    ) -> List[Subscription]:
        """
        Returns active, non-paused subscriptions whose current_period_end has elapsed.
        Explicitly excludes paused subscriptions from automated billing runs.
        """
        cutoff = as_of or datetime.now(timezone.utc)
        return (
            db.query(Subscription)
            .filter(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.is_paused.is_(False),
                Subscription.current_period_end <= cutoff,
            )
            .order_by(Subscription.current_period_end.asc())
            .all()
        )

    @staticmethod
    def get_subscription(db: Session, subscription_id: int) -> Subscription:
        subscription = db.get(Subscription, subscription_id)
        if subscription is None:
            raise SubscriptionNotFoundError(f"Subscription with id {subscription_id} not found.")
        return subscription

    @staticmethod
    def transition_status(
        db: Session,
        subscription_id: int,
        target_status: SubscriptionStatus,
        actor: str = "api",
        reason: Optional[str] = None,
    ) -> Subscription:
        """
        Enforces valid subscription state machine transitions:
        TRIAL -> ACTIVE | CANCELLED
        ACTIVE -> PAST_DUE | CANCELLED
        PAST_DUE -> ACTIVE | CANCELLED
        CANCELLED -> (terminal)
        """
        subscription = SubscriptionService._get_subscription_for_update(db, subscription_id)
        allowed = ALLOWED_SUBSCRIPTION_TRANSITIONS.get(subscription.status, set())
        if target_status not in allowed:
            raise InvalidSubscriptionStateError(
                f"Invalid state transition from '{subscription.status.value}' to '{target_status.value}'."
            )

        old_values = _serialize_subscription(subscription)
        subscription.status = target_status
        if target_status == SubscriptionStatus.CANCELLED:
            subscription.cancelled_at = datetime.now(timezone.utc)
            subscription.cancel_at_period_end = False
            subscription.is_paused = False
            subscription.paused_remaining_seconds = None

        db.flush()
        db.add(
            AuditLog(
                entity_type="subscription",
                entity_id=subscription.id,
                action="subscription.status_changed",
                actor=actor,
                customer_id=subscription.customer_id,
                old_values=old_values,
                new_values=_serialize_subscription(subscription),
                description=reason
                or f"Subscription '{subscription.subscription_code}' transitioned from '{old_values['status']}' to '{target_status.value}'.",
            )
        )
        db.commit()
        db.refresh(subscription)
        return subscription

    @staticmethod
    def _calculate_proration(
        subscription: Subscription,
        new_plan: Plan,
        payload: ProrationPreviewRequest,
    ) -> ProrationPreviewResponse:
        now = ensure_utc(payload.as_of) if payload.as_of else datetime.now(timezone.utc)
        start = ensure_utc(subscription.current_period_start)
        end = ensure_utc(subscription.current_period_end)

        # 1. Calculate total billing period days
        total_days = (end.date() - start.date()).days
        if total_days < 1:
            total_days = max(1, int(round((end - start).total_seconds() / 86400.0)))

        # 2. Calculate remaining billing days
        if now >= end:
            remaining_days = 0
        elif now <= start:
            remaining_days = total_days
        else:
            diff_days = (end.date() - now.date()).days
            remaining_days = max(0, min(total_days, diff_days))

        target_quantity = payload.quantity if payload.quantity is not None else subscription.quantity
        current_recurring = _quantize_money(subscription.unit_price * subscription.quantity)
        new_recurring = _quantize_money(new_plan.price * target_quantity)

        # 3. Calculate unused credit from the current plan
        # 4. Calculate the prorated charge for the new plan
        if total_days > 0 and remaining_days > 0:
            unused_credit = (
                current_recurring * Decimal(remaining_days) / Decimal(total_days)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            prorated_charge = (
                new_recurring * Decimal(remaining_days) / Decimal(total_days)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            unused_credit = Decimal("0.00")
            prorated_charge = Decimal("0.00")

        # 5. Calculate net amount due
        net_amount_due = (prorated_charge - unused_credit).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if new_recurring > current_recurring:
            change_type = "upgrade"
        elif new_recurring < current_recurring:
            change_type = "downgrade"
        else:
            change_type = "switch"

        # Estimated Tax on positive net amount due (GST inclusive)
        tax_rate = settings.DEFAULT_TAX_RATE
        if net_amount_due > Decimal("0.00") and tax_rate > Decimal("0.00"):
            divisor = Decimal("1.00") + tax_rate
            taxable_base = (net_amount_due / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax_amount = (net_amount_due - taxable_base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_due = net_amount_due
        else:
            taxable_base = max(Decimal("0.00"), net_amount_due)
            tax_amount = Decimal("0.00")
            total_due = max(Decimal("0.00"), net_amount_due)
        credit_carried_forward = max(Decimal("0.00"), -net_amount_due)

        current_plan_name = subscription.plan.name if subscription.plan else f"Plan {subscription.plan_id}"
        current_plan_code = subscription.plan.code if subscription.plan else str(subscription.plan_id)

        return ProrationPreviewResponse(
            subscription_id=subscription.id,
            current_plan_id=subscription.plan_id,
            current_plan_name=current_plan_name,
            current_plan_code=current_plan_code,
            current_price=subscription.unit_price,
            current_quantity=subscription.quantity,
            new_plan_id=new_plan.id,
            new_plan_name=new_plan.name,
            new_plan_code=new_plan.code,
            new_price=new_plan.price,
            new_quantity=target_quantity,
            currency=subscription.currency,
            billing_interval=subscription.billing_interval,
            total_billing_days=total_days,
            remaining_billing_days=remaining_days,
            unused_credit=unused_credit,
            prorated_charge=prorated_charge,
            net_amount_due=net_amount_due,
            change_type=change_type,
            tax_amount=tax_amount,
            total_due=total_due,
            credit_carried_forward=credit_carried_forward,
            current_period_start=start,
            current_period_end=end,
            evaluated_at=now,
        )

    @staticmethod
    def preview_proration(
        db: Session,
        subscription_id: int,
        payload: ProrationPreviewRequest,
    ) -> ProrationPreviewResponse:
        """
        Calculates and returns a proration preview without modifying the subscription
        or creating any database records or financial statements.
        """
        subscription = db.get(Subscription, subscription_id)
        if subscription is None:
            raise SubscriptionNotFoundError(f"Subscription with id {subscription_id} not found.")

        if subscription.status == SubscriptionStatus.CANCELLED:
            raise InvalidSubscriptionStateError("Cannot change plan on a cancelled subscription.")
        if subscription.status == SubscriptionStatus.PAST_DUE:
            raise InvalidSubscriptionStateError("Cannot change plan while subscription is past_due.")
        if subscription.is_paused:
            raise InvalidSubscriptionStateError("Cannot change plan while subscription is paused. Resume it first.")

        new_plan = PlanService.ensure_plan_available_for_subscription(db, payload.new_plan_id)

        target_quantity = payload.quantity if payload.quantity is not None else subscription.quantity
        if new_plan.id == subscription.plan_id and target_quantity == subscription.quantity:
            raise InvalidSubscriptionStateError("Subscription is already assigned to this plan and quantity.")

        if new_plan.currency != subscription.currency:
            raise InvalidSubscriptionStateError(
                f"Target plan currency '{new_plan.currency}' does not match subscription currency '{subscription.currency}'."
            )

        return SubscriptionService._calculate_proration(subscription, new_plan, payload)

    @staticmethod
    def change_plan(
        db: Session,
        subscription_id: int,
        payload: SubscriptionPlanChangeRequest,
        actor: str = "api",
    ) -> Subscription:
        """
        Executes a confirmed plan change, calculates proration adjustments,
        creates an itemized adjustment invoice, updates pricing snapshots,
        and logs audit records in a single atomic database transaction.
        """
        subscription = SubscriptionService._get_subscription_for_update(db, subscription_id)
        if subscription.status == SubscriptionStatus.CANCELLED:
            raise InvalidSubscriptionStateError("Cannot change plan on a cancelled subscription.")
        if subscription.status == SubscriptionStatus.PAST_DUE:
            raise InvalidSubscriptionStateError("Cannot change plan while subscription is past_due.")
        if subscription.is_paused:
            raise InvalidSubscriptionStateError("Cannot change plan while subscription is paused. Resume it first.")

        new_plan = PlanService.ensure_plan_available_for_subscription(db, payload.new_plan_id)

        target_quantity = payload.quantity if payload.quantity is not None else subscription.quantity
        if new_plan.id == subscription.plan_id and target_quantity == subscription.quantity:
            raise InvalidSubscriptionStateError("Subscription is already assigned to this plan and quantity.")

        if new_plan.currency != subscription.currency:
            raise InvalidSubscriptionStateError(
                f"Target plan currency '{new_plan.currency}' does not match subscription currency '{subscription.currency}'."
            )

        old_values = _serialize_subscription(subscription)
        old_plan_name = subscription.plan.name if subscription.plan else f"Plan {subscription.plan_id}"
        old_plan_code = subscription.plan.code if subscription.plan else str(subscription.plan_id)
        old_price = subscription.unit_price

        # Calculate exact proration using domain calculation engine
        preview_req = ProrationPreviewRequest(
            new_plan_id=payload.new_plan_id,
            quantity=payload.quantity,
            as_of=payload.as_of,
        )
        proration = SubscriptionService._calculate_proration(
            subscription=subscription,
            new_plan=new_plan,
            payload=preview_req,
        )

        if new_plan.price > old_price:
            change_type = "upgraded"
        elif new_plan.price < old_price:
            change_type = "downgraded"
        else:
            change_type = "switched"

        # Apply new plan pricing snapshot
        subscription.apply_plan_snapshot(new_plan)
        if payload.quantity is not None:
            subscription.quantity = payload.quantity

        if payload.reset_billing_period:
            now = datetime.now(timezone.utc)
            subscription.current_period_start = now
            subscription.current_period_end = calculate_period_end(now, new_plan.billing_interval)

        db.flush()

        # Build adjustment invoice line items
        items: List[InvoiceItemCreate] = []
        if proration.prorated_charge > Decimal("0.00"):
            item_type = (
                InvoiceItemType.PLAN_UPGRADE
                if proration.change_type == "upgrade"
                else InvoiceItemType.PRORATION_DEBIT
            )
            items.append(
                InvoiceItemCreate(
                    item_type=item_type,
                    description=(
                        f"Prorated charge for {new_plan.name} "
                        f"({proration.remaining_billing_days}/{proration.total_billing_days} days remaining)"
                    ),
                    quantity=1,
                    unit_price=proration.prorated_charge,
                    amount=proration.prorated_charge,
                )
            )

        if proration.unused_credit > Decimal("0.00"):
            items.append(
                InvoiceItemCreate(
                    item_type=InvoiceItemType.PRORATION_CREDIT,
                    description=(
                        f"Proration credit for unused {old_plan_name} "
                        f"({proration.remaining_billing_days}/{proration.total_billing_days} days)"
                    ),
                    quantity=1,
                    unit_price=proration.unused_credit,
                    amount=proration.unused_credit,
                )
            )

        if not items:
            items.append(
                InvoiceItemCreate(
                    item_type=InvoiceItemType.PLAN_UPGRADE if proration.change_type == "upgrade" else InvoiceItemType.PRORATION_DEBIT,
                    description=f"Plan change adjustment to {new_plan.name} (0 remaining days)",
                    quantity=1,
                    unit_price=Decimal("0.00"),
                    amount=Decimal("0.00"),
                )
            )

        # Create adjustment invoice within the atomic transaction (commit=False)
        notes = (
            f"Plan change adjustment: {change_type.capitalize()} from {old_plan_name} to {new_plan.name}. "
            f"Prorated charge: {proration.prorated_charge} {subscription.currency}, "
            f"Unused credit: {proration.unused_credit} {subscription.currency}, "
            f"Net: {proration.net_amount_due} {subscription.currency}."
        )
        invoice = InvoiceService.create_itemized_invoice(
            db=db,
            customer_id=subscription.customer_id,
            subscription_id=subscription.id,
            items=items,
            currency=subscription.currency,
            tax_rate=Decimal("0.00"),
            notes=notes,
            actor=actor,
            commit=False,
        )

        db.flush()

        db.add(
            AuditLog(
                entity_type="subscription",
                entity_id=subscription.id,
                action="subscription.plan_changed",
                actor=actor,
                customer_id=subscription.customer_id,
                old_values=old_values,
                new_values=_serialize_subscription(subscription),
                description=(
                    f"Subscription '{subscription.subscription_code}' {change_type} "
                    f"from '{old_plan_code}' to '{new_plan.code}'."
                ),
            )
        )
        db.commit()
        db.refresh(subscription)

        # Store transient attributes on the returned instance
        subscription._last_invoice = invoice
        subscription._last_proration = proration
        return subscription

    @staticmethod
    def get_subscription_history(
        db: Session,
        subscription_id: int,
    ) -> List[SubscriptionHistoryEvent]:
        """
        Compiles a chronological history timeline for a subscription from audit logs
        and financial invoices with actual database records.
        """
        subscription = db.get(Subscription, subscription_id)
        if subscription is None:
            raise SubscriptionNotFoundError(f"Subscription with id {subscription_id} not found.")

        events: List[SubscriptionHistoryEvent] = []

        # 1. Audit logs for subscription
        audit_logs = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == subscription_id)
            .order_by(AuditLog.created_at.desc())
            .all()
        )

        for log in audit_logs:
            title = log.action.replace("subscription.", "").replace("_", " ").title()
            amount = None
            if log.action == "subscription.plan_changed" and log.new_values:
                title = "Plan Changed"
                amount = Decimal(str(log.new_values.get("unit_price", 0)))
            elif log.action == "subscription.created" and log.new_values:
                title = "Subscription Created"
                amount = Decimal(str(log.new_values.get("unit_price", 0)))

            events.append(
                SubscriptionHistoryEvent(
                    id=f"audit-{log.id}",
                    event_type=log.action.replace("subscription.", ""),
                    timestamp=ensure_utc(log.created_at),
                    title=title,
                    description=log.description or log.action,
                    amount=amount,
                    currency=subscription.currency,
                    details=log.new_values,
                )
            )

        # 2. Invoices generated for subscription
        invoices = (
            db.query(Invoice)
            .filter(Invoice.subscription_id == subscription_id)
            .order_by(Invoice.issue_date.desc())
            .all()
        )

        for inv in invoices:
            is_proration = any(
                item.item_type in (
                    InvoiceItemType.PLAN_UPGRADE,
                    InvoiceItemType.PRORATION_DEBIT,
                    InvoiceItemType.PRORATION_CREDIT,
                )
                for item in inv.items
            )
            title = f"Proration Invoice {inv.invoice_number}" if is_proration else f"Invoice {inv.invoice_number}"
            event_type = "proration_invoice" if is_proration else "invoice_generated"
            events.append(
                SubscriptionHistoryEvent(
                    id=f"inv-{inv.id}",
                    event_type=event_type,
                    timestamp=ensure_utc(inv.issue_date),
                    title=title,
                    description=inv.notes or f"Invoice {inv.invoice_number} ({inv.status.value})",
                    amount=inv.total_amount,
                    currency=inv.currency,
                    invoice_id=inv.id,
                    invoice_number=inv.invoice_number,
                    details={
                        "status": inv.status.value,
                        "subtotal": str(inv.subtotal),
                        "discount_amount": str(inv.discount_amount),
                        "tax_amount": str(inv.tax_amount),
                        "total_amount": str(inv.total_amount),
                        "amount_paid": str(inv.amount_paid),
                    },
                )
            )

        # Sort all events chronologically (newest first)
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events

    @staticmethod
    def pause_subscription(
        db: Session,
        subscription_id: int,
        payload: SubscriptionPauseRequest,
        actor: str = "api",
    ) -> Subscription:
        """
        Pauses an active paid subscription using orthogonal is_paused flag while
        preserving SubscriptionStatus.ACTIVE and storing remaining prepaid seconds.
        Uses SELECT ... FOR UPDATE for concurrency safety.
        """
        subscription = SubscriptionService._get_subscription_for_update(db, subscription_id)

        if subscription.status == SubscriptionStatus.CANCELLED:
            raise InvalidSubscriptionStateError("Cancelled subscriptions cannot be paused.")
        if subscription.status == SubscriptionStatus.PAST_DUE:
            raise InvalidSubscriptionStateError("Past-due subscriptions cannot be paused.")
        if subscription.status != SubscriptionStatus.ACTIVE:
            raise InvalidSubscriptionStateError("Only active paid subscriptions can be paused.")
        if subscription.is_paused:
            raise SubscriptionPauseConflictError(
                f"Subscription '{subscription.subscription_code}' is already paused."
            )

        old_values = _serialize_subscription(subscription)
        now = datetime.now(timezone.utc)
        remaining_seconds = max(0, int((subscription.current_period_end - now).total_seconds()))

        subscription.is_paused = True
        subscription.paused_at = now
        subscription.paused_remaining_seconds = remaining_seconds
        subscription.pause_reason = payload.reason

        db.flush()
        db.add(
            AuditLog(
                entity_type="subscription",
                entity_id=subscription.id,
                action="subscription.paused",
                actor=actor,
                customer_id=subscription.customer_id,
                old_values=old_values,
                new_values=_serialize_subscription(subscription),
                description=payload.reason
                or f"Paused subscription '{subscription.subscription_code}' with {remaining_seconds}s remaining.",
            )
        )
        db.commit()
        db.refresh(subscription)
        return subscription

    @staticmethod
    def resume_subscription(
        db: Session,
        subscription_id: int,
        payload: SubscriptionResumeRequest,
        actor: str = "api",
    ) -> Subscription:
        """
        Resumes a currently paused active subscription, restoring remaining prepaid
        duration and extending the renewal boundary accurately.
        Uses SELECT ... FOR UPDATE for concurrency safety.
        """
        subscription = SubscriptionService._get_subscription_for_update(db, subscription_id)

        if subscription.status == SubscriptionStatus.CANCELLED:
            raise InvalidSubscriptionStateError("Cancelled subscriptions can never be resumed.")
        if subscription.status == SubscriptionStatus.PAST_DUE:
            raise InvalidSubscriptionStateError("Past-due subscriptions cannot be resumed directly.")
        if subscription.status != SubscriptionStatus.ACTIVE:
            raise InvalidSubscriptionStateError("Only active subscriptions can be resumed.")
        if not subscription.is_paused:
            raise SubscriptionPauseConflictError(
                f"Subscription '{subscription.subscription_code}' is not paused."
            )

        old_values = _serialize_subscription(subscription)
        now = datetime.now(timezone.utc)
        remaining_seconds = subscription.paused_remaining_seconds or 0

        subscription.is_paused = False
        subscription.resumed_at = now

        if remaining_seconds > 0:
            base_start = max(now, subscription.current_period_start)
            subscription.current_period_end = base_start + timedelta(seconds=remaining_seconds)
        else:
            subscription.current_period_start = now
            subscription.current_period_end = calculate_period_end(now, subscription.billing_interval)

        subscription.paused_remaining_seconds = None

        db.flush()
        db.add(
            AuditLog(
                entity_type="subscription",
                entity_id=subscription.id,
                action="subscription.resumed",
                actor=actor,
                customer_id=subscription.customer_id,
                old_values=old_values,
                new_values=_serialize_subscription(subscription),
                description=payload.reason
                or (
                    f"Resumed subscription '{subscription.subscription_code}'; "
                    f"next renewal at {subscription.current_period_end.isoformat()}."
                ),
            )
        )
        db.commit()
        db.refresh(subscription)
        return subscription

    @staticmethod
    def cancel_subscription(
        db: Session,
        subscription_id: int,
        payload: SubscriptionCancelRequest,
        actor: str = "api",
    ) -> Subscription:
        subscription = SubscriptionService._get_subscription_for_update(db, subscription_id)
        if subscription.status == SubscriptionStatus.CANCELLED:
            raise InvalidSubscriptionStateError("Subscription is already cancelled.")

        old_values = _serialize_subscription(subscription)

        if payload.immediate:
            allowed = ALLOWED_SUBSCRIPTION_TRANSITIONS.get(subscription.status, set())
            if SubscriptionStatus.CANCELLED not in allowed:
                raise InvalidSubscriptionStateError(
                    f"Cannot cancel subscription from '{subscription.status.value}' status."
                )
            subscription.status = SubscriptionStatus.CANCELLED
            subscription.cancelled_at = datetime.now(timezone.utc)
            subscription.cancel_at_period_end = False
            subscription.is_paused = False
            subscription.paused_remaining_seconds = None
            action = "subscription.cancelled"
            default_desc = f"Immediately cancelled subscription '{subscription.subscription_code}'."
        else:
            if subscription.cancel_at_period_end:
                raise InvalidSubscriptionStateError(
                    "Subscription is already scheduled for end-of-billing-cycle cancellation."
                )
            subscription.cancel_at_period_end = True
            action = "subscription.cancel_scheduled"
            default_desc = (
                f"Scheduled end-of-cycle cancellation for subscription '{subscription.subscription_code}' "
                f"at {subscription.current_period_end.isoformat()}."
            )

        db.flush()
        db.add(
            AuditLog(
                entity_type="subscription",
                entity_id=subscription.id,
                action=action,
                actor=actor,
                customer_id=subscription.customer_id,
                old_values=old_values,
                new_values=_serialize_subscription(subscription),
                description=payload.reason or default_desc,
            )
        )
        db.commit()
        db.refresh(subscription)
        return subscription
