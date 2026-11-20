"""
Stage 1 PostgreSQL Model & Migration Tests for Mock Payment Processing:
- Payment idempotency_key & refunded_amount constraints
- Invoice amount_paid <= total_amount constraint & net collected accounting
- PaymentRefund ledger & ON DELETE RESTRICT FKs
- WebhookEvent idempotency & status constraints
- Alembic migration verification strictly against subscription_billing_test_db.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from app.models import (
    Customer,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentRefund,
    PaymentStatus,
    RefundStatus,
    WebhookEvent,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def _seed_invoice(db, total_amount: Decimal = Decimal("100.00"), amount_paid: Decimal = Decimal("0.00"), status: InvoiceStatus = InvoiceStatus.OPEN):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"PAYCUST-{tag}",
        full_name=f"Payment Test User {tag}",
        email=f"pay.test.{tag.lower()}@example.com",
    )
    db.add(customer)
    db.flush()

    invoice = Invoice(
        invoice_number=f"INV-TEST-{tag}",
        customer_id=customer.id,
        subtotal=total_amount,
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=total_amount,
        amount_paid=amount_paid,
        currency="USD",
        status=status,
        issue_date=now,
        due_date=now + timedelta(days=7),
        paid_at=now if status == InvoiceStatus.PAID else None,
    )
    db.add(invoice)
    db.commit()
    db.refresh(customer)
    db.refresh(invoice)
    return customer, invoice


def test_stage1_migration_schema_and_constraints_present(test_engine):
    with test_engine.connect() as conn:
        current_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert current_rev in ("47fd1264f12c", "8a1b2c3d4e5f")

    inspector = inspect(test_engine)
    tables = set(inspector.get_table_names())
    assert {"payments", "payment_refunds", "webhook_events", "invoices"}.issubset(tables)

    payment_cols = {c["name"] for c in inspector.get_columns("payments")}
    assert {"idempotency_key", "refunded_amount"}.issubset(payment_cols)

    payment_uniques = {u["name"] for u in inspector.get_unique_constraints("payments")}
    assert {"uq_payments_transaction_reference", "uq_payments_idempotency_key"}.issubset(payment_uniques)

    payment_checks = {c["name"] for c in inspector.get_check_constraints("payments")}
    assert {"ck_payments_amount_positive", "ck_payments_refunded_amount_valid"}.issubset(payment_checks)

    invoice_checks = {c["name"] for c in inspector.get_check_constraints("invoices")}
    assert "ck_invoices_amount_paid_within_total" in invoice_checks

    refund_uniques = {u["name"] for u in inspector.get_unique_constraints("payment_refunds")}
    assert {"uq_payment_refunds_refund_reference", "uq_payment_refunds_idempotency_key"}.issubset(refund_uniques)

    refund_fks = {tuple(fk["constrained_columns"]): fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("payment_refunds")}
    assert refund_fks[("payment_id",)] == "RESTRICT"
    assert refund_fks[("invoice_id",)] == "RESTRICT"
    assert refund_fks[("customer_id",)] == "RESTRICT"

    webhook_uniques = {u["name"] for u in inspector.get_unique_constraints("webhook_events")}
    assert "uq_webhook_events_event_id" in webhook_uniques

    webhook_fks = {tuple(fk["constrained_columns"]): fk["options"].get("ondelete") for fk in inspector.get_foreign_keys("webhook_events")}
    assert webhook_fks[("payment_id",)] == "RESTRICT"


def test_payment_and_refund_enum_values_unchanged():
    assert [s.value for s in PaymentStatus] == ["pending", "succeeded", "failed", "refunded"]
    assert [s.value for s in RefundStatus] == ["pending", "succeeded", "failed"]


def test_payment_idempotency_key_and_refunded_amount_constraints(db_session):
    customer, invoice = _seed_invoice(db_session, total_amount=Decimal("120.00"))
    tag = _uid()

    # Multiple payments with NULL idempotency_key are permitted
    p1 = Payment(
        transaction_reference=f"TXN-NULL1-{tag}",
        idempotency_key=None,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("60.00"),
        currency="USD",
        payment_method=PaymentMethod.SIMULATED,
        status=PaymentStatus.FAILED,
    )
    p2 = Payment(
        transaction_reference=f"TXN-NULL2-{tag}",
        idempotency_key=None,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("60.00"),
        currency="USD",
        payment_method=PaymentMethod.SIMULATED,
        status=PaymentStatus.SUCCEEDED,
    )
    db_session.add_all([p1, p2])
    db_session.commit()
    db_session.refresh(p2)
    assert p2.refunded_amount == Decimal("0.00")

    # Non-null idempotency_key enforces uniqueness
    p3 = Payment(
        transaction_reference=f"TXN-IDEM1-{tag}",
        idempotency_key=f"IDEM-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("20.00"),
        currency="USD",
        status=PaymentStatus.PENDING,
    )
    db_session.add(p3)
    db_session.commit()

    p4_dup = Payment(
        transaction_reference=f"TXN-IDEM2-{tag}",
        idempotency_key=f"IDEM-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("20.00"),
        currency="USD",
        status=PaymentStatus.PENDING,
    )
    db_session.add(p4_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Negative refunded_amount rejected
    p5_neg_ref = Payment(
        transaction_reference=f"TXN-NEGREF-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("50.00"),
        refunded_amount=Decimal("-0.01"),
        currency="USD",
        status=PaymentStatus.SUCCEEDED,
    )
    db_session.add(p5_neg_ref)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Over-refund (refunded_amount > amount) rejected at DB check constraint level
    p6_over_ref = Payment(
        transaction_reference=f"TXN-OVERREF-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("50.00"),
        refunded_amount=Decimal("50.01"),
        currency="USD",
        status=PaymentStatus.REFUNDED,
    )
    db_session.add(p6_over_ref)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_invoice_amount_paid_within_total_constraint(db_session):
    customer, _ = _seed_invoice(db_session, total_amount=Decimal("100.00"))
    now = datetime.now(timezone.utc)
    tag = _uid()

    overpaid_invoice = Invoice(
        invoice_number=f"INV-OVER-{tag}",
        customer_id=customer.id,
        subtotal=Decimal("50.00"),
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("50.00"),
        amount_paid=Decimal("50.01"),
        currency="USD",
        status=InvoiceStatus.PAID,
        issue_date=now,
        due_date=now + timedelta(days=7),
    )
    db_session.add(overpaid_invoice)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_payment_refund_ledger_constraints_and_restrict_fks(db_session):
    customer, invoice = _seed_invoice(
        db_session,
        total_amount=Decimal("100.00"),
        amount_paid=Decimal("100.00"),
        status=InvoiceStatus.PAID,
    )
    tag = _uid()
    now = datetime.now(timezone.utc)

    payment = Payment(
        transaction_reference=f"TXN-REFUND-SRC-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("100.00"),
        refunded_amount=Decimal("30.00"),
        currency="USD",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=PaymentStatus.SUCCEEDED,
        payment_date=now,
    )
    db_session.add(payment)
    db_session.flush()

    r1 = PaymentRefund(
        refund_reference=f"REF-1-{tag}",
        idempotency_key=f"REF-IDEM-{tag}",
        payment_id=payment.id,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("30.00"),
        currency="USD",
        status=RefundStatus.SUCCEEDED,
        reason="Partial courtesy refund",
        refunded_at=now,
    )
    db_session.add(r1)
    db_session.commit()

    # Duplicate refund_reference rejected
    r_dup_ref = PaymentRefund(
        refund_reference=f"REF-1-{tag}",
        payment_id=payment.id,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("10.00"),
        currency="USD",
        status=RefundStatus.SUCCEEDED,
    )
    db_session.add(r_dup_ref)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Duplicate non-null idempotency_key rejected
    r_dup_idem = PaymentRefund(
        refund_reference=f"REF-2-{tag}",
        idempotency_key=f"REF-IDEM-{tag}",
        payment_id=payment.id,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("10.00"),
        currency="USD",
        status=RefundStatus.SUCCEEDED,
    )
    db_session.add(r_dup_idem)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Zero/negative refund amount rejected
    r_zero = PaymentRefund(
        refund_reference=f"REF-ZERO-{tag}",
        payment_id=payment.id,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("0.00"),
        currency="USD",
        status=RefundStatus.PENDING,
    )
    db_session.add(r_zero)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # RESTRICT foreign keys prevent deleting payment, invoice, or customer with refund history
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM payments WHERE id = :id"), {"id": payment.id})
        db_session.commit()
    db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": invoice.id})
        db_session.commit()
    db_session.rollback()

    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM customers WHERE id = :id"), {"id": customer.id})
        db_session.commit()
    db_session.rollback()


def test_webhook_event_model_uniqueness_status_and_restrict(db_session):
    customer, invoice = _seed_invoice(db_session, total_amount=Decimal("50.00"))
    tag = _uid()

    payment = Payment(
        transaction_reference=f"TXN-WH-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("50.00"),
        currency="USD",
        status=PaymentStatus.PENDING,
    )
    db_session.add(payment)
    db_session.flush()

    ev = WebhookEvent(
        event_id=f"EVT-{tag}",
        gateway="mock_gateway",
        event_type="payment.succeeded",
        payment_id=payment.id,
        transaction_reference=payment.transaction_reference,
        signature_verified=True,
        processing_status="processed",
        payload={"event_id": f"EVT-{tag}", "amount": "50.00"},
        processed_at=datetime.now(timezone.utc),
    )
    db_session.add(ev)
    db_session.commit()

    # Duplicate event_id rejected
    ev_dup = WebhookEvent(
        event_id=f"EVT-{tag}",
        event_type="payment.succeeded",
        signature_verified=True,
        processing_status="processed",
        payload={"event_id": f"EVT-{tag}"},
    )
    db_session.add(ev_dup)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Invalid processing_status rejected
    ev_bad_status = WebhookEvent(
        event_id=f"EVT-BAD-{tag}",
        event_type="payment.succeeded",
        signature_verified=False,
        processing_status="unknown_state",
        payload={"event_id": f"EVT-BAD-{tag}"},
    )
    db_session.add(ev_bad_status)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # RESTRICT FK blocks deleting referenced Payment
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM payments WHERE id = :id"), {"id": payment.id})
        db_session.commit()
    db_session.rollback()


def test_invoice_historical_settlement_preserved_across_partial_and_full_refunds(session_factory):
    """
    Verifies the required accounting rule:
    - Partial refunds retain Payment.status = 'succeeded' and track cumulative refunded_amount.
    - Full refunds transition Payment.status = 'refunded' when refunded_amount == payment.amount.
    - Invoice.amount_paid and InvoiceStatus.PAID remain preserved historically so refunded invoices
      cannot cause accidental automatic re-billing.
    - net_collected_amount accurately reflects (successful payments - successful refunds).
    """
    now = datetime.now(timezone.utc)
    tag = _uid()

    db1 = session_factory()
    try:
        customer, invoice = _seed_invoice(
            db1,
            total_amount=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            status=InvoiceStatus.PAID,
        )
        failed_attempt = Payment(
            transaction_reference=f"TXN-FAIL-{tag}",
            invoice_id=invoice.id,
            customer_id=customer.id,
            amount=Decimal("100.00"),
            currency="USD",
            status=PaymentStatus.FAILED,
            failure_reason="Declined first try",
        )
        settled_payment = Payment(
            transaction_reference=f"TXN-OK-{tag}",
            invoice_id=invoice.id,
            customer_id=customer.id,
            amount=Decimal("100.00"),
            refunded_amount=Decimal("35.00"),
            currency="USD",
            status=PaymentStatus.SUCCEEDED,
            payment_date=now,
        )
        db1.add_all([failed_attempt, settled_payment])
        db1.flush()

        partial_refund = PaymentRefund(
            refund_reference=f"REF-PART-{tag}",
            payment_id=settled_payment.id,
            invoice_id=invoice.id,
            customer_id=customer.id,
            amount=Decimal("35.00"),
            currency="USD",
            status=RefundStatus.SUCCEEDED,
            reason="Partial refund",
            refunded_at=now,
        )
        db1.add(partial_refund)
        db1.commit()
        inv_id, pay_id = invoice.id, settled_payment.id
    finally:
        db1.close()

    # Check partial refund accounting in fresh session
    db2 = session_factory()
    try:
        inv2 = db2.get(Invoice, inv_id)
        pay2 = db2.get(Payment, pay_id)
        assert inv2.status == InvoiceStatus.PAID
        assert inv2.amount_paid == Decimal("100.00")
        assert inv2.total_succeeded_payments == Decimal("100.00")
        assert inv2.total_refunded_amount == Decimal("35.00")
        assert inv2.net_collected_amount == Decimal("65.00")
        assert pay2.status == PaymentStatus.SUCCEEDED
        assert pay2.refunded_amount == Decimal("35.00")
        assert pay2.remaining_refundable_amount == Decimal("65.00")

        # Complete remaining full refund -> Payment becomes REFUNDED, Invoice remains PAID/100.00
        full_remainder = PaymentRefund(
            refund_reference=f"REF-FULL-{tag}",
            payment_id=pay2.id,
            invoice_id=inv2.id,
            customer_id=inv2.customer_id,
            amount=Decimal("65.00"),
            currency="USD",
            status=RefundStatus.SUCCEEDED,
            reason="Full remaining refund",
            refunded_at=now,
        )
        pay2.refunded_amount = Decimal("100.00")
        pay2.status = PaymentStatus.REFUNDED
        db2.add(full_remainder)
        db2.commit()
    finally:
        db2.close()

    db3 = session_factory()
    try:
        inv3 = db3.get(Invoice, inv_id)
        pay3 = db3.get(Payment, pay_id)
        assert inv3.status == InvoiceStatus.PAID
        assert inv3.amount_paid == Decimal("100.00")
        assert inv3.total_succeeded_payments == Decimal("100.00")
        assert inv3.total_refunded_amount == Decimal("100.00")
        assert inv3.net_collected_amount == Decimal("0.00")
        assert pay3.status == PaymentStatus.REFUNDED
        assert pay3.refunded_amount == Decimal("100.00")
        assert pay3.remaining_refundable_amount == Decimal("0.00")
    finally:
        db3.close()

