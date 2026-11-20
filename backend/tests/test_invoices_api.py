"""
Comprehensive Integration & Concurrency Tests for the Phase 3 Invoice Generation Engine.
Executes strictly against subscription_billing_test_db.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError
from app.core.celery_app import celery_app
from app.models import (
    AuditLog,
    BillingCycle,
    BillingCycleStatus,
    BillingInterval,
    Customer,
    Invoice,
    InvoiceItem,
    InvoiceItemType,
    InvoiceStatus,
    Plan,
    Subscription,
    SubscriptionStatus,
)
from app.schemas.invoice import InvoiceItemCreate
from app.services.billing_cycle_service import BillingCycleService
from app.services.billing_dates import ensure_utc
from app.services.invoice_service import BillingCycleNotFoundError, InvoiceService
from app.tasks import invoice_tasks


def _seed_subscription_with_cycle(
    db,
    suffix: str,
    unit_price: Decimal = Decimal("49.00"),
    quantity: int = 1,
    interval: BillingInterval = BillingInterval.MONTHLY,
    cycle_number: int = 1,
    create_cycle: bool = True,
):
    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=30)
    period_start = start_dt
    period_end = now

    plan = Plan(
        name=f"Invoice Plan {suffix}",
        code=f"INVPLN-{suffix}",
        price=unit_price,
        currency="USD",
        billing_interval=interval,
        trial_days=0,
    )
    customer = Customer(
        customer_code=f"INVCUST-{suffix}",
        full_name=f"Invoice User {suffix}",
        email=f"inv.user.{suffix.lower()}@example.com",
    )
    db.add_all([plan, customer])
    db.flush()

    sub = Subscription(
        subscription_code=f"INVSUB-{suffix}",
        customer_id=customer.id,
        plan_id=plan.id,
        status=SubscriptionStatus.ACTIVE,
        quantity=quantity,
        unit_price=unit_price,
        currency="USD",
        billing_interval=interval,
        start_date=start_dt,
        current_period_start=period_start,
        current_period_end=period_end,
    )
    db.add(sub)
    db.flush()

    cycle = None
    if create_cycle:
        cycle = BillingCycle(
            subscription_id=sub.id,
            cycle_number=cycle_number,
            period_start=period_start,
            period_end=period_end,
            billing_date=now,
            status=BillingCycleStatus.PENDING,
            amount=(unit_price * quantity).quantize(Decimal("0.01")),
            currency="USD",
        )
        db.add(cycle)

    db.commit()
    db.refresh(plan)
    db.refresh(customer)
    db.refresh(sub)
    if cycle is not None:
        db.refresh(cycle)
    return plan, customer, sub, cycle


def test_generate_invoice_for_billing_cycle_and_audit_log(db_session):
    _, customer, sub, cycle = _seed_subscription_with_cycle(
        db_session,
        suffix="GEN1",
        unit_price=Decimal("49.99"),
        quantity=2,
    )

    invoice, created = InvoiceService.generate_invoice_for_billing_cycle(
        db=db_session,
        billing_cycle_id=cycle.id,
        tax_rate=Decimal("0.10"),
        notes="Monthly renewal invoice",
        actor="test_runner",
    )

    assert created is True
    assert invoice.invoice_number.startswith(f"INV-{datetime.now(timezone.utc).year}-")
    assert invoice.customer_id == customer.id
    assert invoice.subscription_id == sub.id
    assert invoice.billing_cycle_id == cycle.id
    assert invoice.subtotal == Decimal("99.98")
    assert invoice.discount_amount == Decimal("0.00")
    assert invoice.tax_amount == Decimal("10.00")
    assert invoice.total_amount == Decimal("109.98")
    assert invoice.amount_paid == Decimal("0.00")
    assert invoice.currency == "USD"
    assert invoice.status == InvoiceStatus.OPEN
    assert invoice.due_date > invoice.issue_date
    assert invoice.notes == "Monthly renewal invoice"

    item_types = {item.item_type for item in invoice.items}
    assert item_types == {InvoiceItemType.BASE_SUBSCRIPTION, InvoiceItemType.TAX}
    base_item = next(i for i in invoice.items if i.item_type == InvoiceItemType.BASE_SUBSCRIPTION)
    assert base_item.quantity == 2
    assert base_item.unit_price == Decimal("49.99")
    assert base_item.amount == Decimal("99.98")

    db_session.refresh(cycle)
    assert cycle.status == BillingCycleStatus.COMPLETED
    assert cycle.processed_at is not None

    audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "invoice",
            AuditLog.entity_id == invoice.id,
            AuditLog.action == "invoice.generated",
        )
        .first()
    )
    assert audit is not None
    assert audit.actor == "test_runner"
    assert audit.new_values["invoice_number"] == invoice.invoice_number


def test_invoice_line_item_types_and_proration_calculations(db_session):
    _, customer, sub, _ = _seed_subscription_with_cycle(
        db_session,
        suffix="ITEMS",
        unit_price=Decimal("50.00"),
        create_cycle=False,
    )

    # Case A: All charge/credit types + automatic tax calculation
    inv_auto = InvoiceService.create_itemized_invoice(
        db=db_session,
        customer_id=customer.id,
        subscription_id=sub.id,
        tax_rate=Decimal("0.18"),
        extra_discount=Decimal("5.00"),
        items=[
            InvoiceItemCreate(
                item_type=InvoiceItemType.BASE_SUBSCRIPTION,
                description="Base Monthly Plan",
                quantity=1,
                unit_price=Decimal("50.00"),
                amount=Decimal("50.00"),
            ),
            InvoiceItemCreate(
                item_type=InvoiceItemType.PLAN_UPGRADE,
                description="Mid-cycle Upgrade to Enterprise",
                quantity=1,
                unit_price=Decimal("30.00"),
                amount=Decimal("30.00"),
            ),
            InvoiceItemCreate(
                item_type=InvoiceItemType.PRORATION_DEBIT,
                description="Additional Seat Proration",
                quantity=1,
                unit_price=Decimal("10.00"),
                amount=Decimal("10.00"),
            ),
            InvoiceItemCreate(
                item_type=InvoiceItemType.USAGE,
                description="API Overage 2000 Calls",
                quantity=2,
                unit_price=Decimal("10.00"),
                amount=Decimal("20.00"),
            ),
            InvoiceItemCreate(
                item_type=InvoiceItemType.PRORATION_CREDIT,
                description="Unused Prior Tier Credit",
                quantity=1,
                unit_price=Decimal("15.00"),
                amount=Decimal("15.00"),
            ),
        ],
    )

    # Subtotal = 50 + 30 + 10 + 20 = 110.00; Discount = 15 + 5 = 20.00; Taxable = 90.00; Tax (18%) = 16.20
    assert inv_auto.subtotal == Decimal("110.00")
    assert inv_auto.discount_amount == Decimal("20.00")
    assert inv_auto.tax_amount == Decimal("16.20")
    assert inv_auto.total_amount == Decimal("106.20")
    assert {i.item_type for i in inv_auto.items} == {
        InvoiceItemType.BASE_SUBSCRIPTION,
        InvoiceItemType.PLAN_UPGRADE,
        InvoiceItemType.PRORATION_DEBIT,
        InvoiceItemType.USAGE,
        InvoiceItemType.PRORATION_CREDIT,
        InvoiceItemType.TAX,
    }

    # Case B: Explicit TAX line item takes precedence over tax_rate
    inv_explicit_tax = InvoiceService.create_itemized_invoice(
        db=db_session,
        customer_id=customer.id,
        subscription_id=sub.id,
        tax_rate=Decimal("0.20"),
        items=[
            InvoiceItemCreate(
                item_type=InvoiceItemType.BASE_SUBSCRIPTION,
                description="Base Monthly Plan",
                quantity=1,
                unit_price=Decimal("100.00"),
                amount=Decimal("100.00"),
            ),
            InvoiceItemCreate(
                item_type=InvoiceItemType.TAX,
                description="Explicit Regional Tax",
                quantity=1,
                unit_price=Decimal("4.50"),
                amount=Decimal("4.50"),
            ),
        ],
    )
    assert inv_explicit_tax.subtotal == Decimal("100.00")
    assert inv_explicit_tax.tax_amount == Decimal("4.50")
    assert inv_explicit_tax.total_amount == Decimal("104.50")

    # Case C: Proration credit larger than subtotal floors taxable base and total at 0.00
    inv_credit_floor = InvoiceService.create_itemized_invoice(
        db=db_session,
        customer_id=customer.id,
        subscription_id=sub.id,
        tax_rate=Decimal("0.10"),
        items=[
            InvoiceItemCreate(
                item_type=InvoiceItemType.BASE_SUBSCRIPTION,
                description="Base Monthly Plan",
                quantity=1,
                unit_price=Decimal("20.00"),
                amount=Decimal("20.00"),
            ),
            InvoiceItemCreate(
                item_type=InvoiceItemType.PRORATION_CREDIT,
                description="Downgrade Credit",
                quantity=1,
                unit_price=Decimal("35.00"),
                amount=Decimal("35.00"),
            ),
        ],
    )
    assert inv_credit_floor.subtotal == Decimal("20.00")
    assert inv_credit_floor.discount_amount == Decimal("35.00")
    assert inv_credit_floor.tax_amount == Decimal("0.00")
    assert inv_credit_floor.total_amount == Decimal("0.00")


def test_unique_invoice_numbers_and_duplicate_cycle_prevention(db_session):
    _, _, _, cycle1 = _seed_subscription_with_cycle(db_session, suffix="UNIQ1", unit_price=Decimal("25.00"))
    _, _, _, cycle2 = _seed_subscription_with_cycle(db_session, suffix="UNIQ2", unit_price=Decimal("35.00"))

    inv1, created1 = InvoiceService.generate_invoice_for_billing_cycle(db_session, cycle1.id)
    inv2, created2 = InvoiceService.generate_invoice_for_billing_cycle(db_session, cycle2.id)
    assert created1 is True
    assert created2 is True
    assert inv1.invoice_number != inv2.invoice_number

    seq1 = int(inv1.invoice_number.rsplit("-", 1)[-1])
    seq2 = int(inv2.invoice_number.rsplit("-", 1)[-1])
    assert seq2 > seq1

    # Calling again for cycle1 must return existing invoice cleanly (created=False)
    inv1_dup, created_dup = InvoiceService.generate_invoice_for_billing_cycle(db_session, cycle1.id)
    assert created_dup is False
    assert inv1_dup.id == inv1.id
    assert inv1_dup.invoice_number == inv1.invoice_number
    assert db_session.query(Invoice).filter(Invoice.billing_cycle_id == cycle1.id).count() == 1

    with pytest.raises(BillingCycleNotFoundError):
        InvoiceService.generate_invoice_for_billing_cycle(db_session, 999999)


def test_historical_pricing_snapshot_preserved_when_plan_price_changes(db_session):
    plan, _, sub, _ = _seed_subscription_with_cycle(
        db_session,
        suffix="HIST",
        unit_price=Decimal("39.00"),
        quantity=1,
        create_cycle=False,
    )

    # Raise catalog Plan price dramatically after Subscription was created
    plan.price = Decimal("199.00")
    db_session.commit()
    db_session.refresh(sub)
    assert sub.unit_price == Decimal("39.00")

    now = ensure_utc(sub.current_period_end) + timedelta(minutes=5)
    cycle_res = BillingCycleService.process_due_subscription(
        db=db_session,
        subscription_id=sub.id,
        as_of=now,
        invoice_enqueue_fn=lambda _: None,
    )
    assert cycle_res["status"] == "cycle_created"

    invoice, created = InvoiceService.generate_invoice_for_billing_cycle(
        db=db_session,
        billing_cycle_id=cycle_res["billing_cycle_id"],
    )
    assert created is True
    assert invoice.subtotal == Decimal("39.00")
    assert invoice.total_amount == Decimal("39.00")


def test_multiple_billing_cycles_generate_distinct_invoices(db_session):
    _, _, sub, _ = _seed_subscription_with_cycle(
        db_session,
        suffix="MULTI",
        unit_price=Decimal("60.00"),
        create_cycle=False,
    )

    # Cycle 1 due
    t1 = ensure_utc(sub.current_period_end) + timedelta(minutes=1)
    r1 = BillingCycleService.process_due_subscription(
        db=db_session,
        subscription_id=sub.id,
        as_of=t1,
        invoice_enqueue_fn=lambda _: None,
    )
    assert r1["status"] == "cycle_created"
    assert r1["cycle_number"] == 1

    # Advance past new period end for Cycle 2
    db_session.refresh(sub)
    t2 = ensure_utc(sub.current_period_end) + timedelta(minutes=1)
    r2 = BillingCycleService.process_due_subscription(
        db=db_session,
        subscription_id=sub.id,
        as_of=t2,
        invoice_enqueue_fn=lambda _: None,
    )
    assert r2["status"] == "cycle_created"
    assert r2["cycle_number"] == 2

    inv1, c1 = InvoiceService.generate_invoice_for_billing_cycle(db_session, r1["billing_cycle_id"])
    inv2, c2 = InvoiceService.generate_invoice_for_billing_cycle(db_session, r2["billing_cycle_id"])
    assert c1 is True and c2 is True
    assert inv1.id != inv2.id
    assert inv1.invoice_number != inv2.invoice_number
    assert db_session.query(Invoice).filter(Invoice.subscription_id == sub.id).count() == 2


def test_concurrent_invoice_generation_workers_idempotent(session_factory):
    db = session_factory()
    try:
        _, _, _, cycle = _seed_subscription_with_cycle(
            db,
            suffix="CONCINV",
            unit_price=Decimal("88.00"),
        )
        cycle_id = cycle.id
    finally:
        db.close()

    def _worker():
        worker_db = session_factory()
        try:
            inv, created = InvoiceService.generate_invoice_for_billing_cycle(worker_db, cycle_id)
            return inv.id, inv.invoice_number, created
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: _worker(), range(4)))

    created_flags = [flag for _, _, flag in outcomes]
    invoice_ids = {inv_id for inv_id, _, _ in outcomes}
    invoice_numbers = {inv_num for _, inv_num, _ in outcomes}

    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 3
    assert len(invoice_ids) == 1
    assert len(invoice_numbers) == 1

    db_check = session_factory()
    try:
        assert db_check.query(Invoice).filter(Invoice.billing_cycle_id == cycle_id).count() == 1
    finally:
        db_check.close()


def test_celery_invoice_task_integration_and_retry_behavior(session_factory):
    db = session_factory()
    try:
        _, _, _, cycle = _seed_subscription_with_cycle(
            db,
            suffix="CELINV",
            unit_price=Decimal("75.00"),
        )
        cycle_id = cycle.id
    finally:
        db.close()

    original_always_eager = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        with patch.object(invoice_tasks, "SessionLocal", session_factory):
            res1 = invoice_tasks.generate_invoice_for_billing_cycle.delay(cycle_id).get()
            assert res1["status"] == "invoice_created"
            assert res1["created"] is True
            assert res1["billing_cycle_id"] == cycle_id
            assert res1["total_amount"] == "75.00"
            assert res1["invoice_number"].startswith("INV-")

            # Duplicate Celery delivery must be idempotent
            res2 = invoice_tasks.generate_invoice_for_billing_cycle.delay(cycle_id).get()
            assert res2["status"] == "already_exists"
            assert res2["created"] is False
            assert res2["invoice_id"] == res1["invoice_id"]
            assert res2["invoice_number"] == res1["invoice_number"]

            # Non-existent billing cycle handled cleanly without crashing worker
            res_missing = invoice_tasks.generate_invoice_for_billing_cycle.delay(999999).get()
            assert res_missing["status"] == "billing_cycle_not_found"

            # Transient OperationalError schedules Celery retry
            with patch.object(
                InvoiceService,
                "generate_invoice_for_billing_cycle",
                side_effect=OperationalError("SELECT 1", {}, Exception("db connection lost")),
            ):
                with patch.object(
                    invoice_tasks.generate_invoice_for_billing_cycle,
                    "retry",
                    side_effect=Retry("retry triggered"),
                ) as mock_retry:
                    with pytest.raises(Retry):
                        invoice_tasks.generate_invoice_for_billing_cycle.run(cycle_id)
                    mock_retry.assert_called_once()
    finally:
        celery_app.conf.task_always_eager = original_always_eager


def test_invoice_generation_transaction_rollback_on_failure(session_factory):
    db = session_factory()
    try:
        _, _, _, cycle = _seed_subscription_with_cycle(
            db,
            suffix="ROLLINV",
            unit_price=Decimal("55.00"),
        )
        cycle_id = cycle.id
    finally:
        db.close()

    db_fail = session_factory()
    try:
        with patch.object(db_fail, "commit", side_effect=RuntimeError("Simulated mid-transaction crash")):
            with pytest.raises(RuntimeError, match="Simulated mid-transaction crash"):
                InvoiceService.generate_invoice_for_billing_cycle(db_fail, cycle_id)
        db_fail.rollback()
    finally:
        db_fail.close()

    db_verify = session_factory()
    try:
        assert db_verify.query(Invoice).filter(Invoice.billing_cycle_id == cycle_id).count() == 0
        assert db_verify.query(InvoiceItem).count() == 0
        assert (
            db_verify.query(AuditLog)
            .filter(AuditLog.entity_type == "invoice", AuditLog.action == "invoice.generated")
            .count()
            == 0
        )
        refreshed_cycle = db_verify.get(BillingCycle, cycle_id)
        assert refreshed_cycle.status == BillingCycleStatus.PENDING
    finally:
        db_verify.close()


def test_invoice_api_endpoints_pagination_filters_and_404s(api_client, db_session):
    _, c1, sub1, cy1 = _seed_subscription_with_cycle(db_session, suffix="API1", unit_price=Decimal("30.00"))
    _, c2, sub2, cy2 = _seed_subscription_with_cycle(db_session, suffix="API2", unit_price=Decimal("45.00"))

    inv1, _ = InvoiceService.generate_invoice_for_billing_cycle(db_session, cy1.id)
    inv2, _ = InvoiceService.generate_invoice_for_billing_cycle(db_session, cy2.id)
    inv3 = InvoiceService.create_itemized_invoice(
        db=db_session,
        customer_id=c1.id,
        subscription_id=sub1.id,
        items=[
            InvoiceItemCreate(
                item_type=InvoiceItemType.USAGE,
                description="Overage Charge",
                quantity=1,
                unit_price=Decimal("12.50"),
                amount=Decimal("12.50"),
            )
        ],
    )
    inv2.status = InvoiceStatus.PAID
    db_session.commit()

    # Pagination
    res_page1 = api_client.get("/api/v1/invoices?page=1&size=2")
    assert res_page1.status_code == 200
    body1 = res_page1.json()
    assert body1["total"] == 3
    assert body1["pages"] == 2
    assert len(body1["items"]) == 2

    # Filtering by customer_id, status, subscription_id, billing_cycle_id
    res_paid = api_client.get("/api/v1/invoices?status=paid")
    assert res_paid.status_code == 200
    assert res_paid.json()["total"] == 1
    assert res_paid.json()["items"][0]["id"] == inv2.id

    res_cycle = api_client.get(f"/api/v1/invoices?billing_cycle_id={cy1.id}")
    assert res_cycle.status_code == 200
    assert res_cycle.json()["total"] == 1
    assert res_cycle.json()["items"][0]["id"] == inv1.id

    res_sub = api_client.get(f"/api/v1/invoices?subscription_id={sub2.id}")
    assert res_sub.status_code == 200
    assert res_sub.json()["total"] == 1
    assert res_sub.json()["items"][0]["id"] == inv2.id

    # Get single invoice with nested items
    res_detail = api_client.get(f"/api/v1/invoices/{inv3.id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["invoice_number"] == inv3.invoice_number
    assert detail["total_amount"] == "12.50"
    assert len(detail["items"]) == 1
    assert detail["items"][0]["item_type"] == "usage"

    assert api_client.get("/api/v1/invoices/999999").status_code == 404

    # Customer invoices endpoint
    res_c1 = api_client.get(f"/api/v1/customers/{c1.id}/invoices?page=1&size=10")
    assert res_c1.status_code == 200
    c1_body = res_c1.json()
    assert c1_body["total"] == 2
    assert {item["id"] for item in c1_body["items"]} == {inv1.id, inv3.id}

    res_c2_paid = api_client.get(f"/api/v1/customers/{c2.id}/invoices?status=paid")
    assert res_c2_paid.status_code == 200
    assert res_c2_paid.json()["total"] == 1

    assert api_client.get("/api/v1/customers/999999/invoices").status_code == 404


def test_download_invoice_pdf_endpoint(api_client, db_session):
    _, customer, sub, cycle = _seed_subscription_with_cycle(
        db_session,
        suffix="PDFTEST",
        unit_price=Decimal("49.99"),
        quantity=2,
    )
    invoice, _ = InvoiceService.generate_invoice_for_billing_cycle(
        db=db_session,
        billing_cycle_id=cycle.id,
        tax_rate=Decimal("0.10"),
        notes="Official PDF test invoice notes",
    )

    # 1. Successful PDF download
    res = api_client.get(f"/api/v1/invoices/{invoice.id}/pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert f'filename="invoice-{invoice.invoice_number}.pdf"' in res.headers["content-disposition"]
    pdf_bytes = res.content
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF-")

    # 2. Non-existent invoice returns 404
    res_404 = api_client.get("/api/v1/invoices/999999/pdf")
    assert res_404.status_code == 404


def test_manual_invoice_generation_api_and_search_filters(api_client, db_session):
    _, customer, sub, cycle = _seed_subscription_with_cycle(
        db_session,
        suffix="MANAPI",
        unit_price=Decimal("99.00"),
        quantity=1,
    )

    # 1. Manual Generation via POST /api/v1/invoices/generate
    gen_payload = {
        "billing_cycle_id": cycle.id,
        "tax_rate": 0.08,
        "extra_discount": 10.00,
        "notes": "Custom annual manual invoice",
    }
    res_gen = api_client.post("/api/v1/invoices/generate", json=gen_payload)
    assert res_gen.status_code == 201
    inv_data = res_gen.json()
    assert inv_data["billing_cycle_id"] == cycle.id
    assert inv_data["subtotal"] == "99.00"
    assert inv_data["discount_amount"] == "10.00"
    assert inv_data["customer_name"] == customer.full_name
    assert inv_data["customer_email"] == customer.email
    assert inv_data["status"] == "open"
    assert inv_data["invoice_number"].startswith("INV-")

    # 2. Duplicate generation returns 409 Conflict
    res_dup = api_client.post("/api/v1/invoices/generate", json=gen_payload)
    assert res_dup.status_code == 409
    assert "already exists" in res_dup.json()["detail"]

    # 3. Invalid billing cycle returns 404
    res_missing = api_client.post("/api/v1/invoices/generate", json={"billing_cycle_id": 888888})
    assert res_missing.status_code == 404

    # 4. Search by partial invoice number
    partial_num = inv_data["invoice_number"][-6:]
    res_search = api_client.get(f"/api/v1/invoices?query={partial_num}")
    assert res_search.status_code == 200
    search_json = res_search.json()
    assert search_json["total"] >= 1
    assert any(i["invoice_number"] == inv_data["invoice_number"] for i in search_json["items"])

    # 5. Date filtering
    issue_dt_utc = datetime.fromisoformat(inv_data["issue_date"]).astimezone(timezone.utc)
    res_date = api_client.get(f"/api/v1/invoices?date_from={issue_dt_utc.date().isoformat()}&date_to=2099-12-31")
    assert res_date.status_code == 200
    assert res_date.json()["total"] >= 1


