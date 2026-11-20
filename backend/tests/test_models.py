"""
PostgreSQL Integration Tests for Phase 2 Database Models, Constraints,
Financial Deletion Safety, Historical Pricing, Audit Log Immutability & Test Isolation.
Executes exclusively against subscription_billing_test_db.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, DBAPIError
from app.models import (
    Plan,
    Customer,
    Subscription,
    BillingCycle,
    Invoice,
    InvoiceItem,
    Payment,
    AuditLog,
    BillingInterval,
    SubscriptionStatus,
    BillingCycleStatus,
    InvoiceStatus,
    PaymentStatus,
    PaymentMethod,
)
from tests.conftest import assert_safe_test_database


def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def test_database_isolation_safety_guard(db_session):
    """Verifies tests run strictly on subscription_billing_test_db and reject dev DB."""
    assert db_session.bind.url.database == "subscription_billing_test_db"
    with pytest.raises(RuntimeError, match="SAFETY ABORT"):
        assert_safe_test_database("postgresql+psycopg2://postgres:secret@localhost:5432/subscription_billing_db")


def test_create_all_entities_and_persistence(session_factory):
    """
    Creates all 7 required entities + supporting InvoiceItem in subscription_billing_test_db,
    commits, closes Session 1, re-queries in Session 2 to prove persistence and relationships.
    """
    tag = _uid()
    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30)

    db1 = session_factory()
    try:
        plan = Plan(
            name=f"Pro Plan {tag}",
            code=f"PRO-{tag}",
            description="Professional recurring subscription tier",
            price=Decimal("49.99"),
            currency="USD",
            billing_interval=BillingInterval.MONTHLY,
            trial_days=14,
            features=["Unlimited Invoices", "Automated Dunning", "PDF Statements"],
            is_active=True,
        )
        customer = Customer(
            customer_code=f"CUST-{tag}",
            full_name="Aarav Sharma",
            email=f"aarav.{tag.lower()}@example.com",
            phone="+91-9876543210",
            company_name="Acme FinTech Pvt Ltd",
            billing_address="Bengaluru, Karnataka, India",
            tax_id="29ABCDE1234F1Z5",
            currency="USD",
        )
        db1.add_all([plan, customer])
        db1.flush()

        # Subscription automatically snapshots unit_price, currency, billing_interval from Plan
        subscription = Subscription(
            subscription_code=f"SUB-{tag}",
            customer_id=customer.id,
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            quantity=2,
            start_date=now,
            current_period_start=now,
            current_period_end=period_end,
        )
        db1.add(subscription)
        db1.flush()

        billing_cycle = BillingCycle(
            subscription_id=subscription.id,
            cycle_number=1,
            period_start=now,
            period_end=period_end,
            billing_date=now,
            status=BillingCycleStatus.COMPLETED,
            amount=Decimal("99.98"),
            currency="USD",
            processed_at=now,
        )
        db1.add(billing_cycle)
        db1.flush()

        invoice = Invoice(
            invoice_number=f"INV-{tag}",
            customer_id=customer.id,
            subscription_id=subscription.id,
            billing_cycle_id=billing_cycle.id,
            subtotal=Decimal("99.98"),
            tax_amount=Decimal("18.00"),
            discount_amount=Decimal("0.00"),
            total_amount=Decimal("117.98"),
            amount_paid=Decimal("117.98"),
            currency="USD",
            status=InvoiceStatus.PAID,
            issue_date=now,
            due_date=now + timedelta(days=7),
            paid_at=now,
        )
        db1.add(invoice)
        db1.flush()

        item = InvoiceItem(
            invoice_id=invoice.id,
            description=f"{plan.name} (2 seats)",
            quantity=2,
            unit_price=Decimal("49.99"),
            amount=Decimal("99.98"),
        )
        db1.add(item)

        # Multiple payment attempts for the same invoice (1 failed retry + 1 succeeded)
        failed_payment = Payment(
            transaction_reference=f"TXN-FAIL-{tag}",
            invoice_id=invoice.id,
            customer_id=customer.id,
            amount=Decimal("117.98"),
            currency="USD",
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.FAILED,
            failure_reason="Insufficient funds on initial attempt",
        )
        succeeded_payment = Payment(
            transaction_reference=f"TXN-OK-{tag}",
            invoice_id=invoice.id,
            customer_id=customer.id,
            amount=Decimal("117.98"),
            currency="USD",
            payment_method=PaymentMethod.CREDIT_CARD,
            status=PaymentStatus.SUCCEEDED,
            payment_date=now,
            gateway_response={"gateway": "simulated", "auth_code": f"AUTH-{tag}"},
        )
        db1.add_all([failed_payment, succeeded_payment])

        audit = AuditLog(
            entity_type="invoice",
            entity_id=invoice.id,
            action="invoice.paid",
            actor="system_billing_worker",
            customer_id=customer.id,
            old_values={"status": "open"},
            new_values={"status": "paid", "amount_paid": "117.98"},
            ip_address="127.0.0.1",
            description="Automated payment settled invoice.",
        )
        db1.add(audit)

        db1.commit()
        plan_id, cust_id, sub_id, inv_id = plan.id, customer.id, subscription.id, invoice.id
    finally:
        db1.close()

    # Session 2: Verify real PostgreSQL persistence & relationships
    db2 = session_factory()
    try:
        persisted_plan = db2.query(Plan).filter(Plan.id == plan_id).one()
        assert persisted_plan.price == Decimal("49.99")
        assert persisted_plan.deleted_at is None
        assert persisted_plan.created_at is not None

        persisted_cust = db2.query(Customer).filter(Customer.id == cust_id).one()
        assert len(persisted_cust.subscriptions) == 1
        assert len(persisted_cust.invoices) == 1
        assert len(persisted_cust.payments) == 2

        persisted_sub = db2.query(Subscription).filter(Subscription.id == sub_id).one()
        assert persisted_sub.unit_price == Decimal("49.99")
        assert persisted_sub.currency == "USD"
        assert persisted_sub.billing_interval == BillingInterval.MONTHLY
        assert persisted_sub.billing_cycles[0].invoice.id == inv_id

        persisted_inv = db2.query(Invoice).filter(Invoice.id == inv_id).one()
        assert persisted_inv.total_amount == Decimal("117.98")
        assert len(persisted_inv.items) == 1
        assert len(persisted_inv.payments) == 2
        statuses = {p.status for p in persisted_inv.payments}
        assert statuses == {PaymentStatus.FAILED, PaymentStatus.SUCCEEDED}

        persisted_audit = db2.query(AuditLog).filter(
            AuditLog.entity_id == inv_id, AuditLog.entity_type == "invoice"
        ).one()
        assert persisted_audit.new_values["status"] == "paid"
    finally:
        db2.close()


def test_financial_record_deletion_protection_and_soft_delete(db_session):
    """
    Verifies RESTRICT constraints protect financial records from accidental deletion
    at both SQL and ORM levels, while soft_delete() safely marks Customer/Plan inactive.
    """
    tag = _uid()
    now = datetime.now(timezone.utc)

    plan = Plan(name=f"Biz {tag}", code=f"BIZ-{tag}", price=Decimal("99.00"))
    cust = Customer(customer_code=f"CUST-DEL-{tag}", full_name="Protected Client", email=f"prot.{tag.lower()}@example.com")
    db_session.add_all([plan, cust])
    db_session.flush()

    sub = Subscription(
        subscription_code=f"SUB-DEL-{tag}",
        customer_id=cust.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(sub)
    db_session.flush()

    cycle = BillingCycle(
        subscription_id=sub.id,
        cycle_number=1,
        period_start=now,
        period_end=now + timedelta(days=30),
        billing_date=now,
        amount=Decimal("99.00"),
    )
    db_session.add(cycle)
    db_session.flush()

    inv = Invoice(
        invoice_number=f"INV-DEL-{tag}",
        customer_id=cust.id,
        subscription_id=sub.id,
        billing_cycle_id=cycle.id,
        total_amount=Decimal("99.00"),
        issue_date=now,
        due_date=now + timedelta(days=7),
    )
    db_session.add(inv)
    db_session.flush()

    pay = Payment(
        transaction_reference=f"TX-DEL-{tag}",
        invoice_id=inv.id,
        customer_id=cust.id,
        amount=Decimal("99.00"),
        status=PaymentStatus.SUCCEEDED,
    )
    db_session.add(pay)
    db_session.commit()

    # 1. Attempting to delete Customer with financial records must fail (RESTRICT)
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM customers WHERE id = :id"), {"id": cust.id})
        db_session.commit()
    db_session.rollback()

    # Also verify ORM delete on Customer does NOT delete financial history
    with pytest.raises(IntegrityError):
        c_obj = db_session.get(Customer, cust.id)
        db_session.delete(c_obj)
        db_session.commit()
    db_session.rollback()

    # 2. Attempting to delete Plan referenced by Subscription must fail (RESTRICT)
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM plans WHERE id = :id"), {"id": plan.id})
        db_session.commit()
    db_session.rollback()

    # 3. Attempting to delete Invoice with Payments must fail (RESTRICT)
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": inv.id})
        db_session.commit()
    db_session.rollback()

    # 4. Attempting to delete Subscription with BillingCycles must fail (RESTRICT)
    with pytest.raises(IntegrityError):
        db_session.execute(text("DELETE FROM subscriptions WHERE id = :id"), {"id": sub.id})
        db_session.commit()
    db_session.rollback()

    # 5. Soft deletion marks Customer & Plan inactive with deleted_at, preserving all financial history
    cust_obj = db_session.get(Customer, cust.id)
    plan_obj = db_session.get(Plan, plan.id)
    cust_obj.soft_delete()
    plan_obj.soft_delete()
    db_session.commit()
    db_session.refresh(cust_obj)
    db_session.refresh(plan_obj)

    assert cust_obj.is_deleted is True
    assert cust_obj.is_active is False
    assert cust_obj.deleted_at is not None
    assert plan_obj.is_deleted is True
    assert plan_obj.is_active is False
    assert db_session.get(Invoice, inv.id) is not None
    assert db_session.get(Payment, pay.id) is not None


def test_historical_pricing_preservation(session_factory):
    """
    Verifies changing a Plan's price does not retroactively alter existing Subscriptions,
    while apply_plan_snapshot() updates contracted pricing during an explicit plan upgrade.
    """
    tag = _uid()
    now = datetime.now(timezone.utc)

    db1 = session_factory()
    try:
        starter = Plan(
            name="Starter",
            code=f"START-{tag}",
            price=Decimal("29.00"),
            currency="USD",
            billing_interval=BillingInterval.MONTHLY,
        )
        enterprise = Plan(
            name="Enterprise",
            code=f"ENT-{tag}",
            price=Decimal("299.00"),
            currency="EUR",
            billing_interval=BillingInterval.YEARLY,
        )
        cust = Customer(
            customer_code=f"CUST-HP-{tag}",
            full_name="Price Locked Customer",
            email=f"hp.{tag.lower()}@example.com",
        )
        db1.add_all([starter, enterprise, cust])
        db1.flush()

        # Subscribe to Starter without passing unit_price manually
        sub = Subscription(
            subscription_code=f"SUB-HP-{tag}",
            customer_id=cust.id,
            plan_id=starter.id,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db1.add(sub)
        db1.commit()
        sub_id, starter_id, ent_id = sub.id, starter.id, enterprise.id
    finally:
        db1.close()

    # Change Starter Plan price from 29.00 -> 45.00
    db2 = session_factory()
    try:
        plan_to_edit = db2.get(Plan, starter_id)
        plan_to_edit.price = Decimal("45.00")
        db2.commit()

        existing_sub = db2.get(Subscription, sub_id)
        # Existing subscription retains original $29.00 snapshot!
        assert existing_sub.plan.price == Decimal("45.00")
        assert existing_sub.unit_price == Decimal("29.00")
        assert existing_sub.currency == "USD"
        assert existing_sub.billing_interval == BillingInterval.MONTHLY

        # Now explicitly upgrade subscription to Enterprise plan
        ent_plan = db2.get(Plan, ent_id)
        existing_sub.apply_plan_snapshot(ent_plan)
        db2.commit()
    finally:
        db2.close()

    # Verify upgraded snapshot persisted
    db3 = session_factory()
    try:
        upgraded_sub = db3.get(Subscription, sub_id)
        assert upgraded_sub.plan_id == ent_id
        assert upgraded_sub.unit_price == Decimal("299.00")
        assert upgraded_sub.currency == "EUR"
        assert upgraded_sub.billing_interval == BillingInterval.YEARLY
    finally:
        db3.close()


def test_audit_log_immutability(db_session):
    """
    Verifies both SQLAlchemy ORM listeners and PostgreSQL BEFORE UPDATE/DELETE trigger
    block modifications or deletions of AuditLog records during normal operation,
    while allowing controlled maintenance via session variable when explicitly requested.
    """
    log = AuditLog(
        entity_type="subscription",
        entity_id=101,
        action="subscription.created",
        actor="api_service",
        old_values=None,
        new_values={"status": "active", "unit_price": "49.99"},
    )
    db_session.add(log)
    db_session.commit()
    log_id = log.id

    # 1. ORM UPDATE must raise PermissionError
    with pytest.raises(PermissionError, match="immutable"):
        log.action = "tampered.action"
        db_session.commit()
    db_session.rollback()

    # 2. ORM DELETE must raise PermissionError
    with pytest.raises(PermissionError, match="immutable"):
        obj = db_session.get(AuditLog, log_id)
        db_session.delete(obj)
        db_session.commit()
    db_session.rollback()

    # 3. Direct SQL UPDATE must raise PostgreSQL trigger error (DBAPIError)
    with pytest.raises(DBAPIError, match="Audit logs are immutable"):
        db_session.execute(
            text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
            {"id": log_id},
        )
        db_session.commit()
    db_session.rollback()

    # 4. Direct SQL DELETE must raise PostgreSQL trigger error (DBAPIError)
    with pytest.raises(DBAPIError, match="Audit logs are immutable"):
        db_session.execute(
            text("DELETE FROM audit_logs WHERE id = :id"),
            {"id": log_id},
        )
        db_session.commit()
    db_session.rollback()

    # 5. Verify record remains completely intact
    persisted = db_session.get(AuditLog, log_id)
    assert persisted is not None
    assert persisted.action == "subscription.created"

    # 6. Controlled maintenance procedure works via transaction-local setting
    db_session.execute(text("SET LOCAL app.allow_audit_maintenance = 'on'"))
    db_session.execute(text("DELETE FROM audit_logs WHERE id = :id"), {"id": log_id})
    db_session.commit()
    assert db_session.get(AuditLog, log_id) is None


def test_subscription_status_enum_values(db_session):
    """
    Verifies all four required subscription status values:
    trial, active, past_due, cancelled.
    """
    tag = _uid()
    now = datetime.now(timezone.utc)
    plan = Plan(name=f"Starter {tag}", code=f"START-ST-{tag}", price=Decimal("19.00"))
    cust = Customer(customer_code=f"CUST-ST-{tag}", full_name="Status User", email=f"st.{tag.lower()}@example.com")
    db_session.add_all([plan, cust])
    db_session.flush()

    expected_statuses = [
        SubscriptionStatus.TRIAL,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.CANCELLED,
    ]
    assert [s.value for s in expected_statuses] == ["trial", "active", "past_due", "cancelled"]

    sub_ids = []
    for idx, st in enumerate(expected_statuses):
        sub = Subscription(
            subscription_code=f"SUB-ST-{tag}-{idx}",
            customer_id=cust.id,
            plan_id=plan.id,
            status=st,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(sub)
        db_session.flush()
        sub_ids.append(sub.id)

    db_session.commit()
    saved = [
        s.status.value
        for s in db_session.query(Subscription)
        .filter(Subscription.id.in_(sub_ids))
        .order_by(Subscription.id)
        .all()
    ]
    assert saved == ["trial", "active", "past_due", "cancelled"]


def test_unique_and_check_constraints(db_session):
    """
    Verifies unique constraints, foreign keys, non-negative financials,
    valid period/due dates, and ISO uppercase currency checks.
    """
    tag = _uid()
    now = datetime.now(timezone.utc)

    p1 = Plan(name="P1", code=f"UNQ-{tag}", price=Decimal("10.00"), currency="USD")
    c1 = Customer(customer_code=f"C1-{tag}", full_name="Alice", email=f"alice.{tag.lower()}@example.com")
    db_session.add_all([p1, c1])
    db_session.commit()

    # Duplicate plan code
    db_session.add(Plan(name="P2", code=f"UNQ-{tag}", price=Decimal("20.00")))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Invalid lowercase currency constraint
    db_session.add(Plan(name="BadCurr", code=f"BADCUR-{tag}", price=Decimal("10.00"), currency="usd"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Invalid subscription period boundary (end <= start)
    db_session.add(
        Subscription(
            subscription_code=f"SUB-BADPER-{tag}",
            customer_id=c1.id,
            plan_id=p1.id,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=now,
            current_period_end=now - timedelta(days=1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Invalid invoice due_date < issue_date
    db_session.add(
        Invoice(
            invoice_number=f"INV-BADDUE-{tag}",
            customer_id=c1.id,
            total_amount=Decimal("10.00"),
            issue_date=now,
            due_date=now - timedelta(days=1),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Negative invoice tax_amount
    db_session.add(
        Invoice(
            invoice_number=f"INV-NEGTAX-{tag}",
            customer_id=c1.id,
            subtotal=Decimal("10.00"),
            tax_amount=Decimal("-1.00"),
            total_amount=Decimal("9.00"),
            issue_date=now,
            due_date=now + timedelta(days=7),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
