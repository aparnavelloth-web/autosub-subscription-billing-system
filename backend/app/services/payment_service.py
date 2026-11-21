from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import InvoiceStatus, PaymentMethod, PaymentStatus, RefundStatus, SubscriptionStatus
from app.models.invoice import Invoice
from app.models.payment import Payment, PaymentRefund, WebhookEvent
from app.models.subscription import Subscription
from app.schemas.payment import (
    MockPaymentOutcome,
    MockWebhookEventType,
    MockWebhookPayload,
    PaymentInitiateRequest,
    PaymentProcessRequest,
    PaymentRefundRequest,
    WebhookProcessResponse,
)
from app.services.invoice_service import InvoiceNotFoundError, InvoiceService
from app.services.mock_payment_gateway import MockPaymentGateway

logger = logging.getLogger("subscription_billing.payment_service")


class PaymentNotFoundError(Exception):
    """Raised when a requested payment ID or reference does not exist."""


class InvalidPaymentOperationError(Exception):
    """Raised when a payment request violates invoice state, ownership, currency, or balance rules."""


class DuplicatePaymentError(Exception):
    """Raised when a payment conflicts on transaction_reference or idempotency_key semantics."""


class WebhookConflictError(Exception):
    """Raised when applying a verified webhook violates invoice balance or state constraints."""


def _quantize_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _serialize_payment(payment: Payment) -> Dict[str, Any]:
    return {
        "id": payment.id,
        "transaction_reference": payment.transaction_reference,
        "idempotency_key": payment.idempotency_key,
        "invoice_id": payment.invoice_id,
        "customer_id": payment.customer_id,
        "amount": str(payment.amount),
        "refunded_amount": str(payment.refunded_amount),
        "currency": payment.currency,
        "payment_method": payment.payment_method.value if payment.payment_method else None,
        "status": payment.status.value if payment.status else None,
        "payment_date": payment.payment_date.isoformat() if payment.payment_date else None,
        "failure_reason": payment.failure_reason,
    }


def _serialize_subscription(sub: Subscription) -> Dict[str, Any]:
    return {
        "id": sub.id,
        "subscription_code": sub.subscription_code,
        "customer_id": sub.customer_id,
        "plan_id": sub.plan_id,
        "status": sub.status.value if sub.status else None,
        "currency": sub.currency,
    }


def _serialize_invoice_settlement(invoice: Invoice) -> Dict[str, Any]:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status.value if invoice.status else None,
        "total_amount": str(invoice.total_amount),
        "amount_paid": str(invoice.amount_paid),
        "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
    }


def _matches_idempotent_request(existing: Payment, payload: PaymentInitiateRequest) -> bool:
    if existing.invoice_id != payload.invoice_id:
        return False
    if payload.customer_id is not None and existing.customer_id != payload.customer_id:
        return False
    if payload.amount is not None and existing.amount != _quantize_money(payload.amount):
        return False
    if payload.currency is not None and existing.currency != payload.currency:
        return False
    if existing.payment_method != payload.payment_method:
        return False
    if payload.transaction_reference is not None and existing.transaction_reference != payload.transaction_reference:
        return False
    return True


def _serialize_refund(refund: PaymentRefund) -> Dict[str, Any]:
    return {
        "id": refund.id,
        "refund_reference": refund.refund_reference,
        "idempotency_key": refund.idempotency_key,
        "payment_id": refund.payment_id,
        "invoice_id": refund.invoice_id,
        "customer_id": refund.customer_id,
        "amount": str(refund.amount),
        "currency": refund.currency,
        "status": refund.status.value if refund.status else None,
        "reason": refund.reason,
        "refunded_at": refund.refunded_at.isoformat() if refund.refunded_at else None,
    }


def _matches_idempotent_refund(existing: PaymentRefund, payment_id: int, payload: PaymentRefundRequest) -> bool:
    if existing.payment_id != payment_id:
        return False
    if payload.amount is not None and existing.amount != _quantize_money(payload.amount):
        return False
    if payload.currency is not None and existing.currency != payload.currency:
        return False
    if payload.reason is not None and existing.reason != payload.reason:
        return False
    if payload.refund_reference is not None and existing.refund_reference != payload.refund_reference:
        return False
    return True


class PaymentService:
    """
    Domain service handling mock payment initiation, validation, row-level locking,
    idempotency keys, invoice settlement, and immutable audit logging.
    """

    @staticmethod
    def process_payment(
        db: Session,
        payload: PaymentInitiateRequest,
        actor: str = "api",
        ip_address: Optional[str] = None,
    ) -> Tuple[Payment, bool]:
        """
        Initiates and processes a payment attempt against an OPEN invoice via MockPaymentGateway.
        Returns (Payment, created_flag), where created_flag=False indicates an idempotent replay.
        """
        # Acquire row-level exclusive lock on the target Invoice first so all concurrent
        # attempts against the same invoice execute serially without double-charging.
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == payload.invoice_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if invoice is None:
            raise InvoiceNotFoundError(f"Invoice with id {payload.invoice_id} not found.")

        # Idempotency check BEFORE status validation so retrying a completed key on an
        # already-settled PAID invoice replays cleanly instead of rejecting.
        if payload.idempotency_key is not None:
            existing_by_key = (
                db.query(Payment)
                .filter(Payment.idempotency_key == payload.idempotency_key)
                .first()
            )
            if existing_by_key is not None:
                if _matches_idempotent_request(existing_by_key, payload):
                    return existing_by_key, False
                raise DuplicatePaymentError(
                    f"Idempotency key '{payload.idempotency_key}' was already used for different payment parameters."
                )

        if invoice.status != InvoiceStatus.OPEN:
            raise InvalidPaymentOperationError(
                f"Invoice '{invoice.invoice_number}' cannot accept payments in '{invoice.status.value}' status; "
                "invoice must be 'open'."
            )

        if payload.customer_id is not None and payload.customer_id != invoice.customer_id:
            raise InvalidPaymentOperationError(
                f"Customer id {payload.customer_id} does not match invoice owner {invoice.customer_id}."
            )

        if payload.currency is not None and payload.currency != invoice.currency:
            raise InvalidPaymentOperationError(
                f"Payment currency '{payload.currency}' does not match invoice currency '{invoice.currency}'."
            )

        remaining_balance = _quantize_money(invoice.total_amount - invoice.amount_paid)
        if remaining_balance <= Decimal("0.00"):
            raise InvalidPaymentOperationError(
                f"Invoice '{invoice.invoice_number}' has zero remaining balance ({remaining_balance} {invoice.currency})."
            )

        charge_amount = _quantize_money(payload.amount) if payload.amount is not None else remaining_balance
        if charge_amount <= Decimal("0.00"):
            raise InvalidPaymentOperationError("Payment amount must be greater than 0.00.")

        if charge_amount > remaining_balance:
            raise InvalidPaymentOperationError(
                f"Payment amount {charge_amount} {invoice.currency} exceeds remaining invoice balance "
                f"{remaining_balance} {invoice.currency}."
            )

        pending_sum_raw = (
            db.query(func.coalesce(func.sum(Payment.amount), Decimal("0.00")))
            .filter(
                Payment.invoice_id == invoice.id,
                Payment.status == PaymentStatus.PENDING,
            )
            .scalar()
        )
        pending_total = _quantize_money(Decimal(pending_sum_raw))
        available_uncommitted = _quantize_money(remaining_balance - pending_total)
        if pending_total > Decimal("0.00") and charge_amount > available_uncommitted:
            raise InvalidPaymentOperationError(
                f"Payment amount {charge_amount} {invoice.currency} exceeds uncommitted invoice balance "
                f"{available_uncommitted} {invoice.currency} due to pending payment(s)."
            )

        outcome = payload.simulate_outcome
        if outcome is None or outcome == MockPaymentOutcome.AUTO:
            rand_val = getattr(payload, "random_value", None)
            outcome = MockPaymentGateway.determine_automatic_outcome(random_value=rand_val)

        gateway_result = MockPaymentGateway.charge(
            amount=charge_amount,
            currency=invoice.currency,
            payment_method=payload.payment_method,
            transaction_reference=payload.transaction_reference,
            outcome=outcome,
            failure_reason=payload.failure_reason,
        )

        payment = Payment(
            transaction_reference=gateway_result.transaction_reference,
            idempotency_key=payload.idempotency_key,
            invoice_id=invoice.id,
            customer_id=invoice.customer_id,
            amount=charge_amount,
            refunded_amount=Decimal("0.00"),
            currency=invoice.currency,
            payment_method=payload.payment_method,
            status=gateway_result.status,
            payment_date=gateway_result.processed_at,
            failure_reason=gateway_result.failure_reason,
            gateway_response=gateway_result.gateway_response,
        )
        db.add(payment)

        try:
            db.flush()

            if payment.status == PaymentStatus.SUCCEEDED:
                payment_action = "payment.succeeded"
                payment_desc = (
                    f"Payment '{payment.transaction_reference}' succeeded for invoice "
                    f"'{invoice.invoice_number}' ({payment.amount} {payment.currency})."
                )
            elif payment.status == PaymentStatus.FAILED:
                payment_action = "payment.failed"
                payment_desc = (
                    f"Payment '{payment.transaction_reference}' failed for invoice "
                    f"'{invoice.invoice_number}': {payment.failure_reason}"
                )
                # Failed payment: subscription transitions from Active to Past Due
                if invoice.subscription_id:
                    sub = (
                        db.query(Subscription)
                        .filter(Subscription.id == invoice.subscription_id)
                        .populate_existing()
                        .with_for_update()
                        .first()
                    )
                    if sub is not None and sub.status == SubscriptionStatus.ACTIVE:
                        old_sub = _serialize_subscription(sub)
                        sub.status = SubscriptionStatus.PAST_DUE
                        db.flush()
                        db.add(
                            AuditLog(
                                entity_type="subscription",
                                entity_id=sub.id,
                                action="subscription.past_due",
                                actor=actor,
                                customer_id=sub.customer_id,
                                old_values=old_sub,
                                new_values=_serialize_subscription(sub),
                                ip_address=ip_address,
                                description=(
                                    f"Subscription '{sub.subscription_code}' transitioned to 'past_due' "
                                    f"due to failed payment attempt '{payment.transaction_reference}'."
                                ),
                            )
                        )
            else:
                payment_action = "payment.initiated"
                payment_desc = (
                    f"Payment '{payment.transaction_reference}' initiated in pending state for invoice "
                    f"'{invoice.invoice_number}' ({payment.amount} {payment.currency})."
                )

            db.add(
                AuditLog(
                    entity_type="payment",
                    entity_id=payment.id,
                    action=payment_action,
                    actor=actor,
                    customer_id=invoice.customer_id,
                    old_values=None,
                    new_values=_serialize_payment(payment),
                    ip_address=ip_address,
                    description=payment_desc,
                )
            )

            if payment.status == PaymentStatus.SUCCEEDED:
                old_invoice_values = _serialize_invoice_settlement(invoice)
                invoice.amount_paid = _quantize_money(invoice.amount_paid + charge_amount)
                if invoice.amount_paid == invoice.total_amount:
                    invoice.status = InvoiceStatus.PAID
                    invoice.paid_at = gateway_result.processed_at
                    invoice_action = "invoice.paid"
                else:
                    invoice_action = "invoice.partially_paid"

                db.flush()
                db.add(
                    AuditLog(
                        entity_type="invoice",
                        entity_id=invoice.id,
                        action=invoice_action,
                        actor=actor,
                        customer_id=invoice.customer_id,
                        old_values=old_invoice_values,
                        new_values=_serialize_invoice_settlement(invoice),
                        ip_address=ip_address,
                        description=(
                            f"Applied payment '{payment.transaction_reference}' ({charge_amount} {invoice.currency}) "
                            f"to invoice '{invoice.invoice_number}'."
                        ),
                    )
                )

            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if payload.idempotency_key is not None:
                existing_after = (
                    db.query(Payment)
                    .filter(Payment.idempotency_key == payload.idempotency_key)
                    .first()
                )
                if existing_after is not None:
                    if _matches_idempotent_request(existing_after, payload):
                        return existing_after, False
                    raise DuplicatePaymentError(
                        f"Idempotency key '{payload.idempotency_key}' conflicts with an existing payment."
                    ) from exc
            raise DuplicatePaymentError(
                f"Payment conflicts with an existing transaction_reference or database constraint."
            ) from exc

        db.refresh(payment)
        db.refresh(invoice)
        _ = payment.refunds
        _ = payment.invoice
        _ = payment.customer

        # Automatic Webhook Triggering (Milestone 2 Task 3)
        PaymentService._trigger_automatic_payment_webhook(
            db=db,
            payment=payment,
            invoice=invoice,
            actor=actor,
            ip_address=ip_address,
        )

        return payment, True

    @staticmethod
    def refund_payment(
        db: Session,
        payment_id: int,
        payload: PaymentRefundRequest,
        actor: str = "api",
        ip_address: Optional[str] = None,
    ) -> Tuple[PaymentRefund, bool]:
        """
        Issues a full or partial refund against a settled Payment via MockPaymentGateway.
        Preserves historical Invoice.amount_paid and InvoiceStatus.PAID per accounting rules,
        records PaymentRefund ledger entries, and enforces Invoice -> Payment lock ordering.
        Returns (PaymentRefund, created_flag).
        """
        if payload.amount is not None and payload.amount <= Decimal("0.00"):
            raise InvalidPaymentOperationError("Refund amount must be greater than 0.00.")

        payment_meta = (
            db.query(Payment.id, Payment.invoice_id)
            .filter(Payment.id == payment_id)
            .first()
        )
        if payment_meta is None:
            raise PaymentNotFoundError(f"Payment with id {payment_id} not found.")
        _, target_invoice_id = payment_meta

        # Consistent lock ordering: Invoice FIRST, then Payment (matches process_payment & webhooks)
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == target_invoice_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        payment = (
            db.query(Payment)
            .filter(Payment.id == payment_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if invoice is None or payment is None:
            raise PaymentNotFoundError(f"Payment with id {payment_id} not found.")

        # Idempotency check inside locks BEFORE eligibility/balance validation
        if payload.idempotency_key is not None:
            existing_by_key = (
                db.query(PaymentRefund)
                .filter(PaymentRefund.idempotency_key == payload.idempotency_key)
                .first()
            )
            if existing_by_key is not None:
                if _matches_idempotent_refund(existing_by_key, payment.id, payload):
                    db.add(
                        AuditLog(
                            entity_type="payment_refund",
                            entity_id=existing_by_key.id,
                            action="payment_refund.duplicate_request",
                            actor=actor,
                            customer_id=existing_by_key.customer_id,
                            old_values=None,
                            new_values=_serialize_refund(existing_by_key),
                            ip_address=ip_address,
                            description=(
                                f"Duplicate refund request matched idempotency_key '{payload.idempotency_key}' "
                                f"returning existing refund '{existing_by_key.refund_reference}'."
                            ),
                        )
                    )
                    db.commit()
                    db.refresh(existing_by_key)
                    return existing_by_key, False
                raise DuplicatePaymentError(
                    f"Refund idempotency key '{payload.idempotency_key}' was already used with different parameters."
                )

        # Relationship integrity validation
        if payment.invoice_id != invoice.id or payment.customer_id != invoice.customer_id:
            raise InvalidPaymentOperationError(
                "Payment, invoice, and customer relationships are inconsistent."
            )
        customer = db.get(Customer, payment.customer_id)
        if customer is None:
            raise InvalidPaymentOperationError(f"Customer id {payment.customer_id} not found for payment.")

        if payload.currency is not None and payload.currency != payment.currency:
            raise InvalidPaymentOperationError(
                f"Refund currency '{payload.currency}' does not match payment currency '{payment.currency}'."
            )

        # Only settled (SUCCEEDED) payments can accept new refunds; partial refunds keep status=SUCCEEDED.
        if payment.status != PaymentStatus.SUCCEEDED:
            raise InvalidPaymentOperationError(
                f"Payment '{payment.transaction_reference}' cannot be refunded in '{payment.status.value}' status; "
                "only 'succeeded' payments can be refunded."
            )

        remaining_refundable = _quantize_money(payment.amount - payment.refunded_amount)
        if remaining_refundable <= Decimal("0.00"):
            raise InvalidPaymentOperationError(
                f"Payment '{payment.transaction_reference}' has zero remaining refundable balance."
            )

        pending_refunds_raw = (
            db.query(func.coalesce(func.sum(PaymentRefund.amount), Decimal("0.00")))
            .filter(
                PaymentRefund.payment_id == payment.id,
                PaymentRefund.status == RefundStatus.PENDING,
            )
            .scalar()
        )
        pending_refund_total = _quantize_money(Decimal(pending_refunds_raw))
        available_uncommitted = _quantize_money(remaining_refundable - pending_refund_total)

        refund_amount = (
            _quantize_money(payload.amount)
            if payload.amount is not None
            else available_uncommitted
        )
        if refund_amount <= Decimal("0.00"):
            raise InvalidPaymentOperationError("Refund amount must be greater than 0.00.")

        if refund_amount > remaining_refundable:
            raise InvalidPaymentOperationError(
                f"Refund amount {refund_amount} {payment.currency} exceeds remaining refundable amount "
                f"{remaining_refundable} {payment.currency}."
            )

        if pending_refund_total > Decimal("0.00") and refund_amount > available_uncommitted:
            raise InvalidPaymentOperationError(
                f"Refund amount {refund_amount} {payment.currency} exceeds uncommitted refundable amount "
                f"{available_uncommitted} {payment.currency} due to pending refund(s)."
            )

        gw_refund = MockPaymentGateway.refund(
            amount=refund_amount,
            currency=payment.currency,
            transaction_reference=payment.transaction_reference,
            refund_reference=payload.refund_reference,
            outcome=payload.simulate_outcome,
            reason=payload.reason,
            failure_reason=payload.failure_reason,
        )

        refund = PaymentRefund(
            refund_reference=gw_refund.refund_reference,
            idempotency_key=payload.idempotency_key,
            payment_id=payment.id,
            invoice_id=invoice.id,
            customer_id=payment.customer_id,
            amount=refund_amount,
            currency=payment.currency,
            status=gw_refund.status,
            reason=payload.reason,
            gateway_response=gw_refund.gateway_response,
            refunded_at=gw_refund.refunded_at,
        )
        db.add(refund)

        try:
            db.flush()

            if refund.status == RefundStatus.PENDING:
                db.add(
                    AuditLog(
                        entity_type="payment_refund",
                        entity_id=refund.id,
                        action="payment_refund.initiated",
                        actor=actor,
                        customer_id=payment.customer_id,
                        old_values=None,
                        new_values=_serialize_refund(refund),
                        ip_address=ip_address,
                        description=(
                            f"Initiated pending refund '{refund.refund_reference}' for payment "
                            f"'{payment.transaction_reference}' ({refund.amount} {refund.currency})."
                        ),
                    )
                )
            elif refund.status == RefundStatus.FAILED:
                db.add(
                    AuditLog(
                        entity_type="payment_refund",
                        entity_id=refund.id,
                        action="payment_refund.failed",
                        actor=actor,
                        customer_id=payment.customer_id,
                        old_values=None,
                        new_values=_serialize_refund(refund),
                        ip_address=ip_address,
                        description=(
                            f"Refund '{refund.refund_reference}' failed for payment "
                            f"'{payment.transaction_reference}': {gw_refund.failure_reason}"
                        ),
                    )
                )
            else:
                old_payment = _serialize_payment(payment)
                payment.refunded_amount = _quantize_money(payment.refunded_amount + refund_amount)
                is_full = payment.refunded_amount == payment.amount
                if is_full:
                    payment.status = PaymentStatus.REFUNDED
                    payment_refund_action = "payment.refunded_full"
                    refund_kind = "full"
                else:
                    # Decision 1: partial refunds retain status='succeeded'
                    payment_refund_action = "payment.refunded_partial"
                    refund_kind = "partial"

                db.flush()
                db.add_all(
                    [
                        AuditLog(
                            entity_type="payment_refund",
                            entity_id=refund.id,
                            action="payment_refund.succeeded",
                            actor=actor,
                            customer_id=payment.customer_id,
                            old_values=None,
                            new_values=_serialize_refund(refund),
                            ip_address=ip_address,
                            description=(
                                f"Completed {refund_kind} refund '{refund.refund_reference}' ({refund.amount} "
                                f"{refund.currency}) against payment '{payment.transaction_reference}'."
                            ),
                        ),
                        AuditLog(
                            entity_type="payment",
                            entity_id=payment.id,
                            action=payment_refund_action,
                            actor=actor,
                            customer_id=payment.customer_id,
                            old_values=old_payment,
                            new_values=_serialize_payment(payment),
                            ip_address=ip_address,
                            description=(
                                f"Payment '{payment.transaction_reference}' {refund_kind} refund updated "
                                f"refunded_amount to {payment.refunded_amount}/{payment.amount} {payment.currency}; "
                                f"historical invoice.amount_paid preserved at {invoice.amount_paid}."
                            ),
                        ),
                    ]
                )

            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if payload.idempotency_key is not None:
                existing_after = (
                    db.query(PaymentRefund)
                    .filter(PaymentRefund.idempotency_key == payload.idempotency_key)
                    .first()
                )
                if existing_after is not None:
                    if _matches_idempotent_refund(existing_after, payment_id, payload):
                        return existing_after, False
                    raise DuplicatePaymentError(
                        f"Refund idempotency key '{payload.idempotency_key}' conflicts with an existing refund."
                    ) from exc
            raise DuplicatePaymentError(
                "Refund conflicts with an existing refund_reference or database constraint."
            ) from exc

        db.refresh(refund)
        db.refresh(payment)
        db.refresh(invoice)

        # Automatic Webhook Triggering (Milestone 2 Task 3)
        PaymentService._trigger_automatic_refund_webhook(
            db=db,
            payment=payment,
            refund=refund,
            invoice=invoice,
            actor=actor,
            ip_address=ip_address,
        )

        return refund, True

    @staticmethod
    def _trigger_automatic_payment_webhook(
        db: Session,
        payment: Payment,
        invoice: Invoice,
        actor: str = "payment_processor",
        ip_address: Optional[str] = None,
    ) -> Optional[WebhookEvent]:
        """
        Automatically records and dispatches a verified webhook event after payment processing.
        payment_success for SUCCEEDED, payment_failed for FAILED.
        Runs safely in its own try/except so that any error in webhook recording NEVER rolls back
        or corrupts the already committed payment transaction.
        """
        if payment.status not in (PaymentStatus.SUCCEEDED, PaymentStatus.FAILED):
            return None

        event_type = (
            MockWebhookEventType.PAYMENT_SUCCESS.value
            if payment.status == PaymentStatus.SUCCEEDED
            else MockWebhookEventType.PAYMENT_FAILED_UNDERSCORE.value
        )
        event_id = f"EVT-AUTO-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc)
        payload_dict = {
            "event_id": event_id,
            "event_type": event_type,
            "transaction_reference": payment.transaction_reference,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "status": payment.status.value,
            "gateway_data": payment.gateway_response or {},
        }
        if payment.failure_reason:
            payload_dict["failure_reason"] = payment.failure_reason

        try:
            event_rec = WebhookEvent(
                event_id=event_id,
                gateway=MockPaymentGateway.GATEWAY_NAME,
                event_type=event_type,
                payment_id=payment.id,
                transaction_reference=payment.transaction_reference,
                signature_verified=True,
                processing_status="processed",
                payload=payload_dict,
                processed_at=now,
            )
            db.add(event_rec)
            db.flush()
            db.add(
                AuditLog(
                    entity_type="webhook_event",
                    entity_id=event_rec.id,
                    action="webhook.processed",
                    actor=actor,
                    customer_id=invoice.customer_id,
                    old_values=None,
                    new_values={
                        "event_id": event_rec.event_id,
                        "event_type": event_rec.event_type,
                        "payment_id": payment.id,
                        "processing_status": "processed",
                    },
                    ip_address=ip_address,
                    description=(
                        f"Automatically triggered webhook event '{event_rec.event_id}' ({event_rec.event_type}) "
                        f"for payment '{payment.transaction_reference}'."
                    ),
                )
            )
            db.commit()
            return event_rec
        except Exception as exc:
            db.rollback()
            logger.warning("Automatic webhook dispatch failed for payment %s: %s", payment.id, exc)
            return None

    @staticmethod
    def _trigger_automatic_refund_webhook(
        db: Session,
        payment: Payment,
        refund: PaymentRefund,
        invoice: Invoice,
        actor: str = "refund_processor",
        ip_address: Optional[str] = None,
    ) -> Optional[WebhookEvent]:
        """
        Automatically records and dispatches a verified payment_refunded webhook event after refund completion.
        Runs safely in its own try/except so that any error in webhook recording NEVER rolls back
        or corrupts the already committed refund transaction.
        """
        if refund.status != RefundStatus.SUCCEEDED:
            return None

        event_type = MockWebhookEventType.PAYMENT_REFUNDED.value
        event_id = f"EVT-AUTO-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(timezone.utc)
        payload_dict = {
            "event_id": event_id,
            "event_type": event_type,
            "transaction_reference": payment.transaction_reference,
            "refund_reference": refund.refund_reference,
            "amount": str(refund.amount),
            "currency": refund.currency,
            "status": refund.status.value,
            "gateway_data": refund.gateway_response or {},
        }
        if refund.reason:
            payload_dict["reason"] = refund.reason

        try:
            event_rec = WebhookEvent(
                event_id=event_id,
                gateway=MockPaymentGateway.GATEWAY_NAME,
                event_type=event_type,
                payment_id=payment.id,
                transaction_reference=payment.transaction_reference,
                signature_verified=True,
                processing_status="processed",
                payload=payload_dict,
                processed_at=now,
            )
            db.add(event_rec)
            db.flush()
            db.add(
                AuditLog(
                    entity_type="webhook_event",
                    entity_id=event_rec.id,
                    action="webhook.processed",
                    actor=actor,
                    customer_id=invoice.customer_id,
                    old_values=None,
                    new_values={
                        "event_id": event_rec.event_id,
                        "event_type": event_rec.event_type,
                        "payment_id": payment.id,
                        "refund_id": refund.id,
                        "processing_status": "processed",
                    },
                    ip_address=ip_address,
                    description=(
                        f"Automatically triggered webhook event '{event_rec.event_id}' ({event_rec.event_type}) "
                        f"for refund '{refund.refund_reference}'."
                    ),
                )
            )
            db.commit()
            return event_rec
        except Exception as exc:
            db.rollback()
            logger.warning("Automatic refund webhook dispatch failed for refund %s: %s", refund.id, exc)
            return None

    @staticmethod
    def list_payments(
        db: Session,
        page: int = 1,
        size: int = 20,
        customer_id: Optional[int] = None,
        invoice_id: Optional[int] = None,
        status: Optional[PaymentStatus] = None,
        payment_method: Optional[PaymentMethod] = None,
    ) -> Tuple[List[Payment], int]:
        query = db.query(Payment).options(
            selectinload(Payment.refunds),
            selectinload(Payment.invoice),
            selectinload(Payment.customer),
        )
        if customer_id is not None:
            query = query.filter(Payment.customer_id == customer_id)
        if invoice_id is not None:
            query = query.filter(Payment.invoice_id == invoice_id)
        if status is not None:
            query = query.filter(Payment.status == status)
        if payment_method is not None:
            query = query.filter(Payment.payment_method == payment_method)

        total = query.count()
        items = (
            query.order_by(Payment.id.desc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
        return items, total

    @staticmethod
    def get_payment(db: Session, payment_id: int) -> Payment:
        payment = (
            db.query(Payment)
            .options(
                selectinload(Payment.refunds),
                selectinload(Payment.invoice),
                selectinload(Payment.customer),
            )
            .filter(Payment.id == payment_id)
            .first()
        )
        if payment is None:
            raise PaymentNotFoundError(f"Payment with id {payment_id} not found.")
        return payment

    @staticmethod
    def list_invoice_payments(
        db: Session,
        invoice_id: int,
        page: int = 1,
        size: int = 20,
        status: Optional[PaymentStatus] = None,
    ) -> Tuple[List[Payment], int]:
        InvoiceService.get_invoice(db, invoice_id)
        return PaymentService.list_payments(
            db=db,
            page=page,
            size=size,
            invoice_id=invoice_id,
            status=status,
        )


def _build_duplicate_webhook_response(db: Session, existing: WebhookEvent) -> WebhookProcessResponse:
    payment_status = None
    refund_id = None
    refund_status = None
    invoice_id = None
    invoice_status = None
    if existing.payment_id is not None:
        payment = db.get(Payment, existing.payment_id)
        if payment is not None:
            payment_status = payment.status
            invoice_id = payment.invoice_id
            invoice = db.get(Invoice, payment.invoice_id)
            if invoice is not None:
                invoice_status = invoice.status

    if (
        existing.event_type.startswith("refund.")
        or existing.event_type == MockWebhookEventType.PAYMENT_REFUNDED.value
    ) and isinstance(existing.payload, dict):
        refund_ref = existing.payload.get("refund_reference")
        if refund_ref:
            refund_obj = (
                db.query(PaymentRefund)
                .filter(PaymentRefund.refund_reference == refund_ref)
                .first()
            )
            if refund_obj is not None:
                refund_id = refund_obj.id
                refund_status = refund_obj.status

    return WebhookProcessResponse(
        event_id=existing.event_id,
        event_type=existing.event_type,
        processing_status=existing.processing_status,
        duplicate=True,
        payment_id=existing.payment_id,
        payment_status=payment_status,
        refund_id=refund_id,
        refund_status=refund_status,
        invoice_id=invoice_id,
        invoice_status=invoice_status,
        message=f"Duplicate webhook event '{existing.event_id}' ignored idempotently.",
    )


class WebhookService:
    """
    Processes verified MockPaymentGateway webhook deliveries (`payment.*` and `refund.*`)
    with strict event_id idempotency and consistent Invoice -> Payment -> PaymentRefund
    row locking to eliminate deadlocks with PaymentService.
    """

    @staticmethod
    def process_mock_webhook(
        db: Session,
        payload: MockWebhookPayload,
        raw_payload_dict: Dict[str, Any],
        actor: str = "mock_gateway_webhook",
        ip_address: Optional[str] = None,
    ) -> WebhookProcessResponse:
        # 1. Fast-path idempotency check
        existing_event = (
            db.query(WebhookEvent)
            .filter(WebhookEvent.event_id == payload.event_id)
            .first()
        )
        if existing_event is not None:
            return _build_duplicate_webhook_response(db, existing_event)

        now = datetime.now(timezone.utc)
        if payload.event_type in (
            MockWebhookEventType.PAYMENT_SUCCEEDED,
            MockWebhookEventType.PAYMENT_SUCCESS,
            MockWebhookEventType.PAYMENT_FAILED,
            MockWebhookEventType.PAYMENT_FAILED_UNDERSCORE,
        ):
            return WebhookService._process_payment_webhook(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        if payload.event_type in (
            MockWebhookEventType.REFUND_SUCCEEDED,
            MockWebhookEventType.PAYMENT_REFUNDED,
            MockWebhookEventType.REFUND_FAILED,
        ):
            return WebhookService._process_refund_webhook(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        raise InvalidPaymentOperationError(f"Unsupported webhook event_type '{payload.event_type}'.")

    @staticmethod
    def _record_missing_target(
        *,
        db: Session,
        payload: MockWebhookPayload,
        raw_payload_dict: Dict[str, Any],
        target_ref: Optional[str],
        action: str,
        error_message: str,
        actor: str,
        ip_address: Optional[str],
        now: datetime,
    ) -> WebhookProcessResponse:
        rejected_event = WebhookEvent(
            event_id=payload.event_id,
            gateway=MockPaymentGateway.GATEWAY_NAME,
            event_type=payload.event_type.value,
            payment_id=None,
            transaction_reference=target_ref,
            signature_verified=True,
            processing_status="rejected",
            payload=raw_payload_dict,
            error_message=error_message,
            processed_at=now,
        )
        db.add(rejected_event)
        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="webhook_event",
                    entity_id=rejected_event.id,
                    action=action,
                    actor=actor,
                    customer_id=None,
                    old_values=None,
                    new_values={
                        "event_id": payload.event_id,
                        "event_type": payload.event_type.value,
                        "target_reference": target_ref,
                        "processing_status": "rejected",
                    },
                    ip_address=ip_address,
                    description=error_message,
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raced = (
                db.query(WebhookEvent)
                .filter(WebhookEvent.event_id == payload.event_id)
                .first()
            )
            if raced is not None:
                return _build_duplicate_webhook_response(db, raced)
            raise
        raise PaymentNotFoundError(error_message)

    @staticmethod
    def _process_payment_webhook(
        *,
        db: Session,
        payload: MockWebhookPayload,
        raw_payload_dict: Dict[str, Any],
        actor: str,
        ip_address: Optional[str],
        now: datetime,
    ) -> WebhookProcessResponse:
        payment_meta = (
            db.query(Payment.id, Payment.invoice_id)
            .filter(Payment.transaction_reference == payload.transaction_reference)
            .first()
        )
        if payment_meta is None:
            return WebhookService._record_missing_target(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                target_ref=payload.transaction_reference,
                action="webhook.payment_not_found",
                error_message=f"Payment with transaction_reference '{payload.transaction_reference}' not found.",
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        target_payment_id, target_invoice_id = payment_meta

        # Consistent lock acquisition: Invoice FIRST, then Payment
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == target_invoice_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        payment = (
            db.query(Payment)
            .filter(Payment.id == target_payment_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if invoice is None or payment is None:
            raise PaymentNotFoundError(
                f"Payment or Invoice for transaction_reference '{payload.transaction_reference}' not found."
            )

        existing_locked = (
            db.query(WebhookEvent)
            .filter(WebhookEvent.event_id == payload.event_id)
            .first()
        )
        if existing_locked is not None:
            return _build_duplicate_webhook_response(db, existing_locked)

        # 5. Optional payload amount/currency consistency validation
        if payload.amount is not None and _quantize_money(payload.amount) != payment.amount:
            err_msg = (
                f"Webhook amount {payload.amount} does not match payment amount {payment.amount}."
            )
            return WebhookService._record_rejected_and_raise(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
                action="webhook.rejected_mismatch",
                error_message=err_msg,
                exc_cls=InvalidPaymentOperationError,
            )

        if payload.currency is not None and payload.currency != payment.currency:
            err_msg = (
                f"Webhook currency '{payload.currency}' does not match payment currency '{payment.currency}'."
            )
            return WebhookService._record_rejected_and_raise(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
                action="webhook.rejected_mismatch",
                error_message=err_msg,
                exc_cls=InvalidPaymentOperationError,
            )

        # 6. Dispatch supported Stage 3 event transitions
        if payload.event_type in (
            MockWebhookEventType.PAYMENT_SUCCEEDED,
            MockWebhookEventType.PAYMENT_SUCCESS,
        ):
            if payment.status == PaymentStatus.PENDING:
                remaining_balance = _quantize_money(invoice.total_amount - invoice.amount_paid)
                if invoice.status != InvoiceStatus.OPEN or payment.amount > remaining_balance:
                    conflict_msg = (
                        f"Invoice balance/status conflict for '{invoice.invoice_number}': "
                        f"status='{invoice.status.value}', remaining_balance={remaining_balance}, "
                        f"payment_amount={payment.amount}."
                    )
                    return WebhookService._record_rejected_and_raise(
                        db=db,
                        payload=payload,
                        raw_payload_dict=raw_payload_dict,
                        payment=payment,
                        invoice=invoice,
                        actor=actor,
                        ip_address=ip_address,
                        now=now,
                        action="webhook.invoice_conflict",
                        error_message=conflict_msg,
                        exc_cls=WebhookConflictError,
                    )

                old_payment = _serialize_payment(payment)
                old_invoice = _serialize_invoice_settlement(invoice)

                payment.status = PaymentStatus.SUCCEEDED
                payment.payment_date = now
                payment.failure_reason = None
                payment.gateway_response = {
                    **(payment.gateway_response or {}),
                    "last_webhook_event_id": payload.event_id,
                    "last_webhook_type": payload.event_type.value,
                    "settled_at": now.isoformat(),
                    **({"gateway_data": payload.gateway_data} if payload.gateway_data else {}),
                }

                invoice.amount_paid = _quantize_money(invoice.amount_paid + payment.amount)
                if invoice.amount_paid == invoice.total_amount:
                    invoice.status = InvoiceStatus.PAID
                    invoice.paid_at = now
                    inv_action = "invoice.paid"
                else:
                    inv_action = "invoice.partially_paid"

                event_rec = WebhookEvent(
                    event_id=payload.event_id,
                    gateway=MockPaymentGateway.GATEWAY_NAME,
                    event_type=payload.event_type.value,
                    payment_id=payment.id,
                    transaction_reference=payment.transaction_reference,
                    signature_verified=True,
                    processing_status="processed",
                    payload=raw_payload_dict,
                    processed_at=now,
                )
                db.add(event_rec)

                try:
                    db.flush()
                    db.add_all(
                        [
                            AuditLog(
                                entity_type="payment",
                                entity_id=payment.id,
                                action="payment.succeeded",
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=old_payment,
                                new_values=_serialize_payment(payment),
                                ip_address=ip_address,
                                description=(
                                    f"Settled pending payment '{payment.transaction_reference}' "
                                    f"via webhook '{payload.event_id}'."
                                ),
                            ),
                            AuditLog(
                                entity_type="invoice",
                                entity_id=invoice.id,
                                action=inv_action,
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=old_invoice,
                                new_values=_serialize_invoice_settlement(invoice),
                                ip_address=ip_address,
                                description=(
                                    f"Applied webhook-settled payment '{payment.transaction_reference}' "
                                    f"({payment.amount} {payment.currency}) to invoice '{invoice.invoice_number}'."
                                ),
                            ),
                            AuditLog(
                                entity_type="webhook_event",
                                entity_id=event_rec.id,
                                action="webhook.processed",
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=None,
                                new_values={
                                    "event_id": event_rec.event_id,
                                    "event_type": event_rec.event_type,
                                    "payment_id": payment.id,
                                    "processing_status": "processed",
                                },
                                ip_address=ip_address,
                                description=f"Processed webhook event '{event_rec.event_id}' ({event_rec.event_type}).",
                            ),
                        ]
                    )
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raced = (
                        db.query(WebhookEvent)
                        .filter(WebhookEvent.event_id == payload.event_id)
                        .first()
                    )
                    if raced is not None:
                        return _build_duplicate_webhook_response(db, raced)
                    raise

                db.refresh(payment)
                db.refresh(invoice)
                sub_rec = db.get(Subscription, invoice.subscription_id) if invoice.subscription_id else None
                return WebhookProcessResponse(
                    event_id=event_rec.event_id,
                    event_type=event_rec.event_type,
                    processing_status="processed",
                    duplicate=False,
                    payment_id=payment.id,
                    payment_status=payment.status,
                    invoice_id=invoice.id,
                    invoice_status=invoice.status,
                    subscription_id=sub_rec.id if sub_rec else None,
                    subscription_status=sub_rec.status.value if sub_rec else None,
                    message=f"Payment '{payment.transaction_reference}' settled successfully.",
                )

            # Non-PENDING payment receiving payment.succeeded -> record stale event safely
            return WebhookService._record_ignored_stale(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        if payload.event_type in (
            MockWebhookEventType.PAYMENT_FAILED,
            MockWebhookEventType.PAYMENT_FAILED_UNDERSCORE,
        ):
            if payment.status == PaymentStatus.PENDING:
                old_payment = _serialize_payment(payment)
                payment.status = PaymentStatus.FAILED
                payment.payment_date = None
                payment.failure_reason = (
                    payload.failure_reason or "Payment failed asynchronously per gateway webhook."
                )
                payment.gateway_response = {
                    **(payment.gateway_response or {}),
                    "last_webhook_event_id": payload.event_id,
                    "last_webhook_type": payload.event_type.value,
                    "failed_at": now.isoformat(),
                    **({"gateway_data": payload.gateway_data} if payload.gateway_data else {}),
                }

                # Failed payment: subscription transitions from Active to Past Due
                sub_rec = None
                sub_audit = None
                if invoice.subscription_id:
                    sub_rec = (
                        db.query(Subscription)
                        .filter(Subscription.id == invoice.subscription_id)
                        .populate_existing()
                        .with_for_update()
                        .first()
                    )
                    if sub_rec is not None and sub_rec.status == SubscriptionStatus.ACTIVE:
                        old_sub = _serialize_subscription(sub_rec)
                        sub_rec.status = SubscriptionStatus.PAST_DUE
                        db.flush()
                        sub_audit = AuditLog(
                            entity_type="subscription",
                            entity_id=sub_rec.id,
                            action="subscription.past_due",
                            actor=actor,
                            customer_id=sub_rec.customer_id,
                            old_values=old_sub,
                            new_values=_serialize_subscription(sub_rec),
                            ip_address=ip_address,
                            description=(
                                f"Subscription '{sub_rec.subscription_code}' transitioned to 'past_due' "
                                f"due to failed payment '{payment.transaction_reference}' via webhook '{payload.event_id}'."
                            ),
                        )

                event_rec = WebhookEvent(
                    event_id=payload.event_id,
                    gateway=MockPaymentGateway.GATEWAY_NAME,
                    event_type=payload.event_type.value,
                    payment_id=payment.id,
                    transaction_reference=payment.transaction_reference,
                    signature_verified=True,
                    processing_status="processed",
                    payload=raw_payload_dict,
                    processed_at=now,
                )
                db.add(event_rec)

                try:
                    db.flush()
                    audit_entries = [
                        AuditLog(
                            entity_type="payment",
                            entity_id=payment.id,
                            action="payment.failed",
                            actor=actor,
                            customer_id=invoice.customer_id,
                            old_values=old_payment,
                            new_values=_serialize_payment(payment),
                            ip_address=ip_address,
                            description=(
                                f"Marked pending payment '{payment.transaction_reference}' as failed "
                                f"via webhook '{payload.event_id}': {payment.failure_reason}"
                            ),
                        ),
                        AuditLog(
                            entity_type="webhook_event",
                            entity_id=event_rec.id,
                            action="webhook.processed",
                            actor=actor,
                            customer_id=invoice.customer_id,
                            old_values=None,
                            new_values={
                                "event_id": event_rec.event_id,
                                "event_type": event_rec.event_type,
                                "payment_id": payment.id,
                                "processing_status": "processed",
                            },
                            ip_address=ip_address,
                            description=f"Processed webhook event '{event_rec.event_id}' ({event_rec.event_type}).",
                        ),
                    ]
                    if sub_audit is not None:
                        audit_entries.append(sub_audit)
                    db.add_all(audit_entries)
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raced = (
                        db.query(WebhookEvent)
                        .filter(WebhookEvent.event_id == payload.event_id)
                        .first()
                    )
                    if raced is not None:
                        return _build_duplicate_webhook_response(db, raced)
                    raise

                db.refresh(payment)
                db.refresh(invoice)
                return WebhookProcessResponse(
                    event_id=event_rec.event_id,
                    event_type=event_rec.event_type,
                    processing_status="processed",
                    duplicate=False,
                    payment_id=payment.id,
                    payment_status=payment.status,
                    invoice_id=invoice.id,
                    invoice_status=invoice.status,
                    subscription_id=sub_rec.id if sub_rec else None,
                    subscription_status=sub_rec.status.value if sub_rec else None,
                    message=f"Payment '{payment.transaction_reference}' marked as failed.",
                )

            # Never downgrade SUCCEEDED, REFUNDED, or already FAILED payment
            return WebhookService._record_ignored_stale(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        raise InvalidPaymentOperationError(f"Unsupported webhook event_type '{payload.event_type}'.")

    @staticmethod
    def _process_refund_webhook(
        *,
        db: Session,
        payload: MockWebhookPayload,
        raw_payload_dict: Dict[str, Any],
        actor: str,
        ip_address: Optional[str],
        now: datetime,
    ) -> WebhookProcessResponse:
        refund_meta = None
        if payload.refund_reference:
            refund_meta = (
                db.query(PaymentRefund.id, PaymentRefund.payment_id, PaymentRefund.invoice_id)
                .filter(PaymentRefund.refund_reference == payload.refund_reference)
                .first()
            )
        elif payload.transaction_reference:
            refund_meta = (
                db.query(PaymentRefund.id, PaymentRefund.payment_id, PaymentRefund.invoice_id)
                .join(Payment, Payment.id == PaymentRefund.payment_id)
                .filter(Payment.transaction_reference == payload.transaction_reference)
                .order_by(PaymentRefund.id.desc())
                .first()
            )

        if refund_meta is None:
            target_ref = payload.refund_reference or payload.transaction_reference
            return WebhookService._record_missing_target(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                target_ref=target_ref,
                action="webhook.refund_not_found",
                error_message=f"Refund target '{target_ref}' not found.",
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        target_refund_id, target_payment_id, target_invoice_id = refund_meta

        # Strict deadlock-free lock order: Invoice -> Payment -> PaymentRefund
        invoice = (
            db.query(Invoice)
            .filter(Invoice.id == target_invoice_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        payment = (
            db.query(Payment)
            .filter(Payment.id == target_payment_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        refund = (
            db.query(PaymentRefund)
            .filter(PaymentRefund.id == target_refund_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if invoice is None or payment is None or refund is None:
            raise PaymentNotFoundError(
                f"Refund target for refund_reference '{payload.refund_reference}' not found."
            )

        existing_locked = (
            db.query(WebhookEvent)
            .filter(WebhookEvent.event_id == payload.event_id)
            .first()
        )
        if existing_locked is not None:
            return _build_duplicate_webhook_response(db, existing_locked)

        if payload.transaction_reference and payload.transaction_reference != payment.transaction_reference:
            return WebhookService._record_rejected_and_raise(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
                action="webhook.rejected_mismatch",
                error_message=(
                    f"Webhook transaction_reference '{payload.transaction_reference}' "
                    f"does not match refund parent payment '{payment.transaction_reference}'."
                ),
                exc_cls=InvalidPaymentOperationError,
            )

        if payload.amount is not None and _quantize_money(payload.amount) != refund.amount:
            return WebhookService._record_rejected_and_raise(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
                action="webhook.rejected_mismatch",
                error_message=f"Webhook amount {payload.amount} does not match refund amount {refund.amount}.",
                exc_cls=InvalidPaymentOperationError,
            )

        if payload.currency is not None and payload.currency != refund.currency:
            return WebhookService._record_rejected_and_raise(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                actor=actor,
                ip_address=ip_address,
                now=now,
                action="webhook.rejected_mismatch",
                error_message=f"Webhook currency '{payload.currency}' does not match refund currency '{refund.currency}'.",
                exc_cls=InvalidPaymentOperationError,
            )

        if payload.event_type in (
            MockWebhookEventType.REFUND_SUCCEEDED,
            MockWebhookEventType.PAYMENT_REFUNDED,
        ):
            if refund.status == RefundStatus.PENDING:
                new_refunded_total = _quantize_money(payment.refunded_amount + refund.amount)
                if payment.status != PaymentStatus.SUCCEEDED or new_refunded_total > payment.amount:
                    return WebhookService._record_rejected_and_raise(
                        db=db,
                        payload=payload,
                        raw_payload_dict=raw_payload_dict,
                        payment=payment,
                        invoice=invoice,
                        actor=actor,
                        ip_address=ip_address,
                        now=now,
                        action="webhook.refund_conflict",
                        error_message=(
                            f"Refund settlement conflict for payment '{payment.transaction_reference}': "
                            f"status='{payment.status.value}', refunded_amount={payment.refunded_amount}, "
                            f"refund_amount={refund.amount}, payment_amount={payment.amount}."
                        ),
                        exc_cls=WebhookConflictError,
                    )

                old_refund = _serialize_refund(refund)
                old_payment = _serialize_payment(payment)

                refund.status = RefundStatus.SUCCEEDED
                refund.refunded_at = now
                refund.gateway_response = {
                    **(refund.gateway_response or {}),
                    "last_webhook_event_id": payload.event_id,
                    "last_webhook_type": payload.event_type.value,
                    "settled_at": now.isoformat(),
                    **({"gateway_data": payload.gateway_data} if payload.gateway_data else {}),
                }

                payment.refunded_amount = new_refunded_total
                is_full = payment.refunded_amount == payment.amount
                if is_full:
                    payment.status = PaymentStatus.REFUNDED
                    payment_action = "payment.refunded_full"
                    refund_kind = "full"
                else:
                    # Partial refunds retain Payment.status = SUCCEEDED
                    payment_action = "payment.refunded_partial"
                    refund_kind = "partial"

                event_rec = WebhookEvent(
                    event_id=payload.event_id,
                    gateway=MockPaymentGateway.GATEWAY_NAME,
                    event_type=payload.event_type.value,
                    payment_id=payment.id,
                    transaction_reference=payment.transaction_reference,
                    signature_verified=True,
                    processing_status="processed",
                    payload=raw_payload_dict,
                    processed_at=now,
                )
                db.add(event_rec)

                try:
                    db.flush()
                    db.add_all(
                        [
                            AuditLog(
                                entity_type="payment_refund",
                                entity_id=refund.id,
                                action="payment_refund.succeeded",
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=old_refund,
                                new_values=_serialize_refund(refund),
                                ip_address=ip_address,
                                description=(
                                    f"Settled pending {refund_kind} refund '{refund.refund_reference}' "
                                    f"via webhook '{payload.event_id}'."
                                ),
                            ),
                            AuditLog(
                                entity_type="payment",
                                entity_id=payment.id,
                                action=payment_action,
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=old_payment,
                                new_values=_serialize_payment(payment),
                                ip_address=ip_address,
                                description=(
                                    f"Webhook {refund_kind} refund updated refunded_amount to "
                                    f"{payment.refunded_amount}/{payment.amount} {payment.currency}; "
                                    f"invoice.amount_paid preserved at {invoice.amount_paid}."
                                ),
                            ),
                            AuditLog(
                                entity_type="webhook_event",
                                entity_id=event_rec.id,
                                action="webhook.processed",
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=None,
                                new_values={
                                    "event_id": event_rec.event_id,
                                    "event_type": event_rec.event_type,
                                    "payment_id": payment.id,
                                    "refund_id": refund.id,
                                    "processing_status": "processed",
                                },
                                ip_address=ip_address,
                                description=f"Processed webhook event '{event_rec.event_id}' ({event_rec.event_type}).",
                            ),
                        ]
                    )
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raced = (
                        db.query(WebhookEvent)
                        .filter(WebhookEvent.event_id == payload.event_id)
                        .first()
                    )
                    if raced is not None:
                        return _build_duplicate_webhook_response(db, raced)
                    raise

                db.refresh(refund)
                db.refresh(payment)
                db.refresh(invoice)
                return WebhookProcessResponse(
                    event_id=event_rec.event_id,
                    event_type=event_rec.event_type,
                    processing_status="processed",
                    duplicate=False,
                    payment_id=payment.id,
                    payment_status=payment.status,
                    refund_id=refund.id,
                    refund_status=refund.status,
                    invoice_id=invoice.id,
                    invoice_status=invoice.status,
                    message=f"Refund '{refund.refund_reference}' settled successfully.",
                )

            return WebhookService._record_ignored_stale(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                refund=refund,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        if payload.event_type == MockWebhookEventType.REFUND_FAILED:
            if refund.status == RefundStatus.PENDING:
                old_refund = _serialize_refund(refund)
                fail_msg = payload.failure_reason or "Refund failed asynchronously per gateway webhook."
                refund.status = RefundStatus.FAILED
                refund.refunded_at = None
                refund.gateway_response = {
                    **(refund.gateway_response or {}),
                    "last_webhook_event_id": payload.event_id,
                    "last_webhook_type": payload.event_type.value,
                    "failure_reason": fail_msg,
                    "failed_at": now.isoformat(),
                    **({"gateway_data": payload.gateway_data} if payload.gateway_data else {}),
                }

                event_rec = WebhookEvent(
                    event_id=payload.event_id,
                    gateway=MockPaymentGateway.GATEWAY_NAME,
                    event_type=payload.event_type.value,
                    payment_id=payment.id,
                    transaction_reference=payment.transaction_reference,
                    signature_verified=True,
                    processing_status="processed",
                    payload=raw_payload_dict,
                    processed_at=now,
                )
                db.add(event_rec)

                try:
                    db.flush()
                    db.add_all(
                        [
                            AuditLog(
                                entity_type="payment_refund",
                                entity_id=refund.id,
                                action="payment_refund.failed",
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=old_refund,
                                new_values=_serialize_refund(refund),
                                ip_address=ip_address,
                                description=f"Marked pending refund '{refund.refund_reference}' as failed via webhook '{payload.event_id}'.",
                            ),
                            AuditLog(
                                entity_type="webhook_event",
                                entity_id=event_rec.id,
                                action="webhook.processed",
                                actor=actor,
                                customer_id=invoice.customer_id,
                                old_values=None,
                                new_values={
                                    "event_id": event_rec.event_id,
                                    "event_type": event_rec.event_type,
                                    "payment_id": payment.id,
                                    "refund_id": refund.id,
                                    "processing_status": "processed",
                                },
                                ip_address=ip_address,
                                description=f"Processed webhook event '{event_rec.event_id}' ({event_rec.event_type}).",
                            ),
                        ]
                    )
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raced = (
                        db.query(WebhookEvent)
                        .filter(WebhookEvent.event_id == payload.event_id)
                        .first()
                    )
                    if raced is not None:
                        return _build_duplicate_webhook_response(db, raced)
                    raise

                db.refresh(refund)
                db.refresh(payment)
                db.refresh(invoice)
                return WebhookProcessResponse(
                    event_id=event_rec.event_id,
                    event_type=event_rec.event_type,
                    processing_status="processed",
                    duplicate=False,
                    payment_id=payment.id,
                    payment_status=payment.status,
                    refund_id=refund.id,
                    refund_status=refund.status,
                    invoice_id=invoice.id,
                    invoice_status=invoice.status,
                    message=f"Refund '{refund.refund_reference}' marked as failed.",
                )

            # Never reverse an already SUCCEEDED refund or re-fail a FAILED refund
            return WebhookService._record_ignored_stale(
                db=db,
                payload=payload,
                raw_payload_dict=raw_payload_dict,
                payment=payment,
                invoice=invoice,
                refund=refund,
                actor=actor,
                ip_address=ip_address,
                now=now,
            )

        raise InvalidPaymentOperationError(f"Unsupported webhook event_type '{payload.event_type}'.")

    @staticmethod
    def _record_ignored_stale(
        *,
        db: Session,
        payload: MockWebhookPayload,
        raw_payload_dict: Dict[str, Any],
        payment: Payment,
        invoice: Invoice,
        actor: str,
        ip_address: Optional[str],
        now: datetime,
        refund: Optional[PaymentRefund] = None,
    ) -> WebhookProcessResponse:
        if refund is not None:
            msg = (
                f"Ignored stale '{payload.event_type.value}' webhook because refund "
                f"'{refund.refund_reference}' is already '{refund.status.value}'."
            )
            old_snapshot = _serialize_refund(refund)
            new_snapshot = {
                "event_id": payload.event_id,
                "event_type": payload.event_type.value,
                "payment_id": payment.id,
                "refund_id": refund.id,
                "refund_status": refund.status.value,
                "processing_status": "ignored_stale",
            }
        else:
            msg = (
                f"Ignored stale '{payload.event_type.value}' webhook because payment "
                f"'{payment.transaction_reference}' is already '{payment.status.value}'."
            )
            old_snapshot = _serialize_payment(payment)
            new_snapshot = {
                "event_id": payload.event_id,
                "event_type": payload.event_type.value,
                "payment_id": payment.id,
                "payment_status": payment.status.value,
                "processing_status": "ignored_stale",
            }

        event_rec = WebhookEvent(
            event_id=payload.event_id,
            gateway=MockPaymentGateway.GATEWAY_NAME,
            event_type=payload.event_type.value,
            payment_id=payment.id,
            transaction_reference=payment.transaction_reference,
            signature_verified=True,
            processing_status="ignored_stale",
            payload=raw_payload_dict,
            error_message=msg,
            processed_at=now,
        )
        db.add(event_rec)
        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="webhook_event",
                    entity_id=event_rec.id,
                    action="webhook.ignored_stale",
                    actor=actor,
                    customer_id=invoice.customer_id,
                    old_values=old_snapshot,
                    new_values=new_snapshot,
                    ip_address=ip_address,
                    description=msg,
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raced = (
                db.query(WebhookEvent)
                .filter(WebhookEvent.event_id == payload.event_id)
                .first()
            )
            if raced is not None:
                return _build_duplicate_webhook_response(db, raced)
            raise

        return WebhookProcessResponse(
            event_id=event_rec.event_id,
            event_type=event_rec.event_type,
            processing_status="ignored_stale",
            duplicate=False,
            payment_id=payment.id,
            payment_status=payment.status,
            refund_id=refund.id if refund is not None else None,
            refund_status=refund.status if refund is not None else None,
            invoice_id=invoice.id,
            invoice_status=invoice.status,
            message=msg,
        )

    @staticmethod
    def _record_rejected_and_raise(
        *,
        db: Session,
        payload: MockWebhookPayload,
        raw_payload_dict: Dict[str, Any],
        payment: Payment,
        invoice: Invoice,
        actor: str,
        ip_address: Optional[str],
        now: datetime,
        action: str,
        error_message: str,
        exc_cls,
    ) -> WebhookProcessResponse:
        event_rec = WebhookEvent(
            event_id=payload.event_id,
            gateway=MockPaymentGateway.GATEWAY_NAME,
            event_type=payload.event_type.value,
            payment_id=payment.id,
            transaction_reference=payment.transaction_reference,
            signature_verified=True,
            processing_status="rejected",
            payload=raw_payload_dict,
            error_message=error_message,
            processed_at=now,
        )
        db.add(event_rec)
        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="webhook_event",
                    entity_id=event_rec.id,
                    action=action,
                    actor=actor,
                    customer_id=invoice.customer_id,
                    old_values=_serialize_invoice_settlement(invoice),
                    new_values={
                        "event_id": event_rec.event_id,
                        "event_type": event_rec.event_type,
                        "payment_id": payment.id,
                        "processing_status": "rejected",
                        "error_message": error_message,
                    },
                    ip_address=ip_address,
                    description=error_message,
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            raced = (
                db.query(WebhookEvent)
                .filter(WebhookEvent.event_id == payload.event_id)
                .first()
            )
            if raced is not None:
                return _build_duplicate_webhook_response(db, raced)
            raise
        raise exc_cls(error_message)

