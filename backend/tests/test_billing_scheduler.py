"""
Comprehensive Integration & Unit Tests for Phase 3 Billing Cycle Scheduler,
Celery Configuration, Idempotency, Renewal Date Math, Concurrency & Retry Behavior.
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
    Plan,
    Subscription,
    SubscriptionStatus,
)
from app.services.billing_cycle_service import BillingCycleService
from app.services.billing_dates import add_calendar_months, calculate_period_end, ensure_utc
from app.services.invoice_service import InvoiceService
from app.tasks import billing_tasks, invoice_tasks


def _seed_subscription(
    db,
    code_suffix: str,
    interval: BillingInterval = BillingInterval.MONTHLY,
    price: Decimal = Decimal("49.00"),
    quantity: int = 1,
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    is_paused: bool = False,
    cancel_at_period_end: bool = False,
    start_date: datetime = None,
    period_start: datetime = None,
    period_end: datetime = None,
):
    now = datetime.now(timezone.utc)
    start_dt = ensure_utc(start_date or (now - timedelta(days=35)))
    p_start = ensure_utc(period_start or start_dt)
    p_end = ensure_utc(period_end or (now - timedelta(hours=1)))

    plan = Plan(
        name=f"Plan {code_suffix}",
        code=f"PLN-{code_suffix}",
        price=price,
        currency="USD",
        billing_interval=interval,
        trial_days=0,
    )
    customer = Customer(
        customer_code=f"CUST-{code_suffix}",
        full_name=f"User {code_suffix}",
        email=f"user.{code_suffix.lower()}@example.com",
    )
    db.add_all([plan, customer])
    db.flush()

    sub = Subscription(
        subscription_code=f"SUB-{code_suffix}",
        customer_id=customer.id,
        plan_id=plan.id,
        status=status,
        quantity=quantity,
        unit_price=price,
        currency="USD",
        billing_interval=interval,
        start_date=start_dt,
        current_period_start=p_start,
        current_period_end=p_end,
        is_paused=is_paused,
        cancel_at_period_end=cancel_at_period_end,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def test_celery_configuration_and_beat_schedule():
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_url.startswith("redis://")
    assert celery_app.conf.result_backend.startswith("redis://")

    assert "process-due-billing-cycles" in celery_app.conf.beat_schedule
    beat_entry = celery_app.conf.beat_schedule["process-due-billing-cycles"]
    assert beat_entry["task"] == "app.tasks.billing_tasks.process_due_subscriptions_task"
    assert "app.tasks.billing_tasks.process_due_subscriptions_task" in celery_app.tasks
    assert "app.tasks.invoice_tasks.generate_invoice_for_billing_cycle" in celery_app.tasks


def test_calendar_renewal_date_calculations_month_end_and_leap_year():
    # Monthly standard
    mar15 = datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc)
    assert calculate_period_end(mar15, BillingInterval.MONTHLY) == datetime(2026, 4, 15, 10, 30, tzinfo=timezone.utc)

    # Month-end clamping (non-leap year 2026: Jan 31 -> Feb 28 -> Mar 31 -> Apr 30)
    jan31 = datetime(2026, 1, 31, 8, 0, tzinfo=timezone.utc)
    feb28 = calculate_period_end(jan31, BillingInterval.MONTHLY, anchor_day=31)
    assert feb28 == datetime(2026, 2, 28, 8, 0, tzinfo=timezone.utc)
    mar31 = calculate_period_end(feb28, BillingInterval.MONTHLY, anchor_day=31)
    assert mar31 == datetime(2026, 3, 31, 8, 0, tzinfo=timezone.utc)
    apr30 = calculate_period_end(mar31, BillingInterval.MONTHLY, anchor_day=31)
    assert apr30 == datetime(2026, 4, 30, 8, 0, tzinfo=timezone.utc)

    # Leap year Feb 29 handling (2024 leap year)
    leap_jan31 = datetime(2024, 1, 31, 12, 0, tzinfo=timezone.utc)
    leap_feb29 = calculate_period_end(leap_jan31, BillingInterval.MONTHLY, anchor_day=31)
    assert leap_feb29 == datetime(2024, 2, 29, 12, 0, tzinfo=timezone.utc)

    # Annual renewal from leap day -> 2025-02-28 -> 2028-02-29
    yr_2025 = calculate_period_end(leap_feb29, BillingInterval.YEARLY, anchor_day=29)
    assert yr_2025 == datetime(2025, 2, 28, 12, 0, tzinfo=timezone.utc)
    yr_2028 = add_calendar_months(yr_2025, 36, anchor_day=29)
    assert yr_2028 == datetime(2028, 2, 29, 12, 0, tzinfo=timezone.utc)

    # Non-UTC timezone normalized to UTC
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    ist_dt = datetime(2026, 5, 10, 5, 30, tzinfo=ist_tz)
    assert ensure_utc(ist_dt) == datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)


def test_due_detection_and_eligibility_exclusions(session_factory):
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        assert db.bind.url.database == "subscription_billing_test_db"
        due_sub = _seed_subscription(db, "DUE", period_end=now - timedelta(minutes=10), quantity=2, price=Decimal("30.00"))
        future_sub = _seed_subscription(db, "FUT", period_end=now + timedelta(days=10))
        paused_sub = _seed_subscription(db, "PAU", period_end=now - timedelta(days=1), is_paused=True)
        cancelled_sub = _seed_subscription(db, "CAN", period_end=now - timedelta(days=1), status=SubscriptionStatus.CANCELLED)
        past_due_sub = _seed_subscription(db, "PAS", period_end=now - timedelta(days=1), status=SubscriptionStatus.PAST_DUE)
        trial_sub = _seed_subscription(db, "TRI", period_end=now - timedelta(days=1), status=SubscriptionStatus.TRIAL)
        due_id, fut_id, pau_id, can_id, pas_id, tri_id = (
            due_sub.id, future_sub.id, paused_sub.id, cancelled_sub.id, past_due_sub.id, trial_sub.id
        )
    finally:
        db.close()

    enqueued_cycles = []
    summary = BillingCycleService.run_billing_sweep(
        session_factory=session_factory,
        as_of=now,
        invoice_enqueue_fn=lambda cid: enqueued_cycles.append(cid),
    )

    assert summary["due_found"] == 1
    assert summary["cycles_created"] == 1
    assert summary["errors"] == 0
    assert len(enqueued_cycles) == 1

    db_check = session_factory()
    try:
        cycles = db_check.query(BillingCycle).all()
        assert len(cycles) == 1
        assert cycles[0].subscription_id == due_id
        assert cycles[0].cycle_number == 1
        assert cycles[0].amount == Decimal("60.00")
        assert cycles[0].status == BillingCycleStatus.PENDING
        assert enqueued_cycles[0] == cycles[0].id

        # Non-due / paused / cancelled / past_due / trial subscriptions untouched
        for excluded_id in (fut_id, pau_id, can_id, pas_id, tri_id):
            assert db_check.query(BillingCycle).filter(BillingCycle.subscription_id == excluded_id).count() == 0
    finally:
        db_check.close()


def test_end_of_cycle_cancellation_respected(session_factory):
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        sub = _seed_subscription(
            db,
            "EOC",
            period_end=now - timedelta(minutes=5),
            cancel_at_period_end=True,
        )
        sub_id = sub.id
    finally:
        db.close()

    enqueued = []
    summary = BillingCycleService.run_billing_sweep(
        session_factory=session_factory,
        as_of=now,
        invoice_enqueue_fn=lambda cid: enqueued.append(cid),
    )

    assert summary["due_found"] == 1
    assert summary["cycles_created"] == 0
    assert summary["cancelled_at_period_end"] == 1
    assert enqueued == []

    db_check = session_factory()
    try:
        updated = db_check.get(Subscription, sub_id)
        assert updated.status == SubscriptionStatus.CANCELLED
        assert updated.cancelled_at is not None
        assert updated.cancel_at_period_end is False
        assert db_check.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).count() == 0

        audit = (
            db_check.query(AuditLog)
            .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == sub_id, AuditLog.action == "subscription.cancelled")
            .one()
        )
        assert audit.actor == "celery_beat_scheduler"
    finally:
        db_check.close()


def test_monthly_annual_and_month_end_renewal_advancement(session_factory):
    jan31 = datetime(2026, 1, 31, 9, 0, tzinfo=timezone.utc)
    feb28 = datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
    leap_feb29 = datetime(2024, 2, 29, 9, 0, tzinfo=timezone.utc)

    db = session_factory()
    try:
        mo_sub = _seed_subscription(
            db,
            "MOEND",
            interval=BillingInterval.MONTHLY,
            start_date=jan31,
            period_start=jan31,
            period_end=feb28,
        )
        yr_sub = _seed_subscription(
            db,
            "YRLEAP",
            interval=BillingInterval.YEARLY,
            start_date=datetime(2023, 2, 28, 9, 0, tzinfo=timezone.utc),
            period_start=datetime(2023, 2, 28, 9, 0, tzinfo=timezone.utc),
            period_end=leap_feb29,
        )
        mo_id, yr_id = mo_sub.id, yr_sub.id
    finally:
        db.close()

    # Sweep on 2026-02-28 -> mo_sub advances Feb 28 -> Mar 31 (restoring Jan 31 anchor!)
    # and yr_sub (period_end 2024-02-29 <= 2026-02-28) advances Feb 29, 2024 -> Feb 28, 2025!
    enqueued = []
    summary = BillingCycleService.run_billing_sweep(
        session_factory=session_factory,
        as_of=feb28,
        invoice_enqueue_fn=lambda cid: enqueued.append(cid),
    )
    assert summary["cycles_created"] == 2

    db_check = session_factory()
    try:
        refreshed_mo = db_check.get(Subscription, mo_id)
        assert refreshed_mo.current_period_start == feb28
        assert refreshed_mo.current_period_end == datetime(2026, 3, 31, 9, 0, tzinfo=timezone.utc)

        refreshed_yr = db_check.get(Subscription, yr_id)
        assert refreshed_yr.current_period_start == leap_feb29
        assert refreshed_yr.current_period_end == datetime(2025, 2, 28, 9, 0, tzinfo=timezone.utc)
    finally:
        db_check.close()


def test_duplicate_cycle_prevention_and_idempotency(session_factory):
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        sub = _seed_subscription(db, "IDEMP", period_end=now - timedelta(minutes=1))
        sub_id = sub.id
    finally:
        db.close()

    enqueued = []
    # Run sweep twice at the exact same timestamp
    s1 = BillingCycleService.run_billing_sweep(session_factory, as_of=now, invoice_enqueue_fn=enqueued.append)
    s2 = BillingCycleService.run_billing_sweep(session_factory, as_of=now, invoice_enqueue_fn=enqueued.append)
    assert s1["cycles_created"] == 1
    assert s2["due_found"] == 0
    assert s2["cycles_created"] == 0
    assert len(enqueued) == 1

    # Also test direct idempotency against (subscription_id, cycle_number) unique constraint
    db2 = session_factory()
    try:
        cycle_dup, created = BillingCycleService.create_billing_cycle_idempotent(
            db=db2,
            subscription_id=sub_id,
            cycle_number=1,
            period_start=now,
            period_end=now + timedelta(days=30),
            billing_date=now,
            amount=Decimal("49.00"),
            currency="USD",
        )
        assert created is False
        assert cycle_dup.cycle_number == 1
        assert db2.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).count() == 1
    finally:
        db2.close()


def test_concurrent_workers_prevent_duplicate_billing_cycles(session_factory):
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        sub = _seed_subscription(db, "CONC", period_end=now - timedelta(minutes=5))
        sub_id = sub.id
    finally:
        db.close()

    enqueued = []

    def _worker_run():
        worker_db = session_factory()
        try:
            return BillingCycleService.process_due_subscription(
                db=worker_db,
                subscription_id=sub_id,
                as_of=now,
                invoice_enqueue_fn=lambda cid: enqueued.append(cid),
            )
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda _: _worker_run(), range(3)))

    statuses = [o["status"] for o in outcomes]
    assert statuses.count("cycle_created") == 1
    assert statuses.count("skipped_not_due") == 2
    assert len(enqueued) == 1

    db_verify = session_factory()
    try:
        assert db_verify.query(BillingCycle).filter(BillingCycle.subscription_id == sub_id).count() == 1
    finally:
        db_verify.close()


def test_celery_tasks_eager_handoff_and_retry_behavior(session_factory):
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        sub = _seed_subscription(db, "CEL", period_end=now - timedelta(minutes=5), price=Decimal("99.00"))
        sub_id = sub.id
    finally:
        db.close()

    # Wire Celery task SessionLocal strictly to subscription_billing_test_db
    original_always_eager = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        with patch.object(billing_tasks, "SessionLocal", session_factory), \
             patch.object(invoice_tasks, "SessionLocal", session_factory):
            async_res = billing_tasks.process_due_subscriptions_task.delay(as_of_iso=now.isoformat())
            summary = async_res.get()
            assert summary["cycles_created"] == 1
            assert summary["results"][0]["invoice_task_enqueued"] is True

            # Verify eager scheduler handoff already created a real Invoice record in DB
            cycle_id = summary["results"][0]["billing_cycle_id"]
            db_check = session_factory()
            try:
                handoff_invoices = (
                    db_check.query(Invoice)
                    .filter(Invoice.billing_cycle_id == cycle_id)
                    .all()
                )
                assert len(handoff_invoices) == 1
                handoff_invoice = handoff_invoices[0]
                assert handoff_invoice.billing_cycle_id == cycle_id
                assert handoff_invoice.subscription_id == sub_id
                assert handoff_invoice.total_amount == Decimal("99.00")
                assert handoff_invoice.invoice_number.startswith("INV-")
            finally:
                db_check.close()

            # Calling the invoice task again for the same cycle must be idempotent ("already_exists")
            inv_repeat_res = invoice_tasks.generate_invoice_for_billing_cycle.delay(cycle_id).get()
            assert inv_repeat_res["status"] == "already_exists"
            assert inv_repeat_res["created"] is False
            assert inv_repeat_res["billing_cycle_id"] == cycle_id
            assert inv_repeat_res["invoice_id"] == handoff_invoice.id
            assert inv_repeat_res["invoice_number"] == handoff_invoice.invoice_number
            assert inv_repeat_res["total_amount"] == "99.00"

            # Verify direct invoice task invocation creates a new invoice first, then idempotently skips duplicates
            db_direct = session_factory()
            try:
                sub2 = _seed_subscription(
                    db_direct,
                    "CEL2",
                    period_end=now + timedelta(days=25),
                    price=Decimal("99.00"),
                )
                direct_cycle = BillingCycle(
                    subscription_id=sub2.id,
                    cycle_number=1,
                    period_start=now - timedelta(days=5),
                    period_end=now + timedelta(days=25),
                    billing_date=now,
                    status=BillingCycleStatus.PENDING,
                    amount=Decimal("99.00"),
                    currency="USD",
                )
                db_direct.add(direct_cycle)
                db_direct.commit()
                db_direct.refresh(direct_cycle)
                direct_cycle_id = direct_cycle.id
            finally:
                db_direct.close()

            inv_created_res = invoice_tasks.generate_invoice_for_billing_cycle.delay(direct_cycle_id).get()
            assert inv_created_res["status"] == "invoice_created"
            assert inv_created_res["created"] is True
            assert inv_created_res["billing_cycle_id"] == direct_cycle_id
            assert inv_created_res["total_amount"] == "99.00"
            assert inv_created_res["invoice_number"].startswith("INV-")

            inv_dup_res = invoice_tasks.generate_invoice_for_billing_cycle.delay(direct_cycle_id).get()
            assert inv_dup_res["status"] == "already_exists"
            assert inv_dup_res["created"] is False
            assert inv_dup_res["billing_cycle_id"] == direct_cycle_id
            assert inv_dup_res["invoice_id"] == inv_created_res["invoice_id"]

            db_verify = session_factory()
            try:
                assert db_verify.query(Invoice).filter(Invoice.billing_cycle_id == cycle_id).count() == 1
                created_invoices = (
                    db_verify.query(Invoice)
                    .filter(Invoice.billing_cycle_id == direct_cycle_id)
                    .all()
                )
                assert len(created_invoices) == 1
                assert created_invoices[0].id == inv_created_res["invoice_id"]
                assert created_invoices[0].billing_cycle_id == direct_cycle_id
            finally:
                db_verify.close()

        # Test retry behavior on transient OperationalError for both billing and invoice tasks
        with patch.object(
            BillingCycleService,
            "run_billing_sweep",
            side_effect=OperationalError("SELECT 1", {}, Exception("connection dropped")),
        ):
            with patch.object(
                billing_tasks.process_due_subscriptions_task,
                "retry",
                side_effect=Retry("retry triggered"),
            ) as mock_retry:
                with pytest.raises(Retry):
                    billing_tasks.process_due_subscriptions_task.run(as_of_iso=now.isoformat())
                mock_retry.assert_called_once()

        with patch.object(
            InvoiceService,
            "generate_invoice_for_billing_cycle",
            side_effect=OperationalError("SELECT 1", {}, Exception("connection dropped")),
        ):
            with patch.object(
                invoice_tasks.generate_invoice_for_billing_cycle,
                "retry",
                side_effect=Retry("invoice retry triggered"),
            ) as mock_inv_retry:
                with patch.object(invoice_tasks, "SessionLocal", session_factory):
                    with pytest.raises(Retry):
                        invoice_tasks.generate_invoice_for_billing_cycle.run(cycle_id)
                mock_inv_retry.assert_called_once()
    finally:
        celery_app.conf.task_always_eager = original_always_eager


def test_database_transaction_rollback_and_broker_failure_safety(session_factory):
    now = datetime.now(timezone.utc)
    db = session_factory()
    try:
        sub = _seed_subscription(db, "TXSAFE", period_end=now - timedelta(minutes=5))
        sub_id = sub.id
        orig_end = sub.current_period_end
    finally:
        db.close()

    # 1. Simulate broker failure during invoice enqueue -> DB commit for BillingCycle stays intact safely
    def _broken_broker(_cycle_id: int):
        raise ConnectionError("Redis broker unreachable")

    res = BillingCycleService.run_billing_sweep(
        session_factory=session_factory,
        as_of=now,
        invoice_enqueue_fn=_broken_broker,
    )
    assert res["cycles_created"] == 1
    assert res["results"][0]["invoice_task_enqueued"] is False
    assert "Redis broker unreachable" in res["results"][0]["dispatch_error"]

    # 2. Simulate mid-transaction DB failure -> Subscription & BillingCycle roll back cleanly
    db_fail = session_factory()
    try:
        sub2 = _seed_subscription(db_fail, "TXROLL", period_end=now - timedelta(minutes=5))
        sub2_id = sub2.id
        sub2_orig_end = sub2.current_period_end
    finally:
        db_fail.close()

    with patch.object(
        BillingCycleService,
        "create_billing_cycle_idempotent",
        side_effect=RuntimeError("Simulated crash before commit"),
    ):
        fail_summary = BillingCycleService.run_billing_sweep(session_factory=session_factory, as_of=now)
        assert fail_summary["errors"] == 1

    db_verify = session_factory()
    try:
        sub2_check = db_verify.get(Subscription, sub2_id)
        assert ensure_utc(sub2_check.current_period_end) == ensure_utc(sub2_orig_end)
        assert db_verify.query(BillingCycle).filter(BillingCycle.subscription_id == sub2_id).count() == 0
    finally:
        db_verify.close()

