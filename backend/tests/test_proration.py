"""
Comprehensive test suite for Milestone 2 Task 2: Proration and Plan Change Logic.
Validates:
- Exact day calculations (total days, remaining days)
- Unused credit calculation
- Prorated charge calculation
- Net amount due calculation (upgrades, downgrades, switches)
- Zero remaining days & full remaining days edge cases
- Preview endpoint (read-only, zero side-effects)
- Confirmed plan change (atomic invoice generation with line items, status, audit logging)
- Same-plan, currency mismatch, archived plan, and invalid status rejections
- Decimal precision & ROUND_HALF_UP currency rounding
- Transaction safety & atomic rollback
- Subscription history timeline endpoint
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.models.audit_log import AuditLog
from app.models.enums import (
    BillingInterval,
    InvoiceItemType,
    InvoiceStatus,
    SubscriptionStatus,
)
from app.models.invoice import Invoice
from app.models.subscription import Subscription
from app.services.subscription_service import SubscriptionService


def _setup_proration_fixtures(api_client):
    """Creates an active customer and standard plans for proration testing."""
    uid = uuid.uuid4().hex[:8]
    cust_res = api_client.post(
        "/api/v1/customers",
        json={
            "full_name": f"Proration Test User {uid}",
            "email": f"proration_{uid}@example.com",
            "currency": "INR",
        },
    )
    assert cust_res.status_code == 201
    cust = cust_res.json()

    # Plan A: Pro (₹1000/month)
    plan_pro = api_client.post(
        "/api/v1/plans",
        json={
            "code": f"PRO-{uid}",
            "name": "Pro Plan",
            "price": "1000.00",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    # Plan B: Premium (₹2000/month)
    plan_prem = api_client.post(
        "/api/v1/plans",
        json={
            "code": f"PREM-{uid}",
            "name": "Premium Plan",
            "price": "2000.00",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    # Plan C: Starter (₹500/month)
    plan_start = api_client.post(
        "/api/v1/plans",
        json={
            "code": f"START-{uid}",
            "name": "Starter Plan",
            "price": "500.00",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    # Plan D: Foreign currency (USD)
    plan_usd = api_client.post(
        "/api/v1/plans",
        json={
            "code": f"USD-{uid}",
            "name": "USD Plan",
            "price": "50.00",
            "currency": "USD",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    return cust, plan_pro, plan_prem, plan_start, plan_usd


def test_mentor_example_proration_preview(api_client, db_session):
    """
    Validates the mentor's explicit specification:
    Current plan: Pro — ₹1000/month
    New plan: Premium — ₹2000/month
    Remaining days: 15
    Total billing period: 30 days

    Unused credit = ₹500.00
    New plan charge = ₹1000.00
    Net amount due = ₹500.00
    """
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    # Create active subscription on Pro
    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_pro["id"], "status": "active"},
    )
    assert sub_res.status_code == 201
    sub_data = sub_res.json()
    sub_id = sub_data["id"]

    # Set exact 30-day window: May 1 to May 31
    start_dt = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    as_of_dt = datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)  # exactly 15 days remaining

    sub_obj = db_session.get(Subscription, sub_id)
    sub_obj.current_period_start = start_dt
    sub_obj.current_period_end = end_dt
    db_session.commit()

    # Call proration preview
    preview_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": plan_prem["id"], "as_of": as_of_dt.isoformat()},
    )
    assert preview_res.status_code == 200
    data = preview_res.json()

    assert data["subscription_id"] == sub_id
    assert data["current_plan_id"] == plan_pro["id"]
    assert data["current_plan_name"] == "Pro Plan"
    assert Decimal(data["current_price"]) == Decimal("1000.00")
    assert data["new_plan_id"] == plan_prem["id"]
    assert data["new_plan_name"] == "Premium Plan"
    assert Decimal(data["new_price"]) == Decimal("2000.00")

    assert data["total_billing_days"] == 30
    assert data["remaining_billing_days"] == 15
    assert Decimal(data["unused_credit"]) == Decimal("500.00")
    assert Decimal(data["prorated_charge"]) == Decimal("1000.00")
    assert Decimal(data["net_amount_due"]) == Decimal("500.00")
    assert Decimal(data["total_due"]) == Decimal("500.00")
    assert data["change_type"] == "upgrade"

    # Verify preview is strictly read-only: no invoices created, subscription unit_price unchanged
    db_session.refresh(sub_obj)
    assert sub_obj.unit_price == Decimal("1000.00")
    invoices_count = db_session.query(Invoice).filter(Invoice.subscription_id == sub_id).count()
    assert invoices_count == 0


def test_proration_preview_downgrade(api_client, db_session):
    """
    Validates downgrade proration:
    Current plan: Premium — ₹2000/month
    New plan: Pro — ₹1000/month
    Remaining days: 15 / 30 days

    Unused credit = ₹1000.00
    New plan charge = ₹500.00
    Net amount due = -₹500.00
    Total due = ₹0.00
    Credit carried forward = ₹500.00
    """
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_prem["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    start_dt = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)  # 30 days
    as_of_dt = datetime(2026, 6, 16, 0, 0, 0, tzinfo=timezone.utc)  # 15 days remaining

    sub_obj = db_session.get(Subscription, sub_id)
    sub_obj.current_period_start = start_dt
    sub_obj.current_period_end = end_dt
    db_session.commit()

    preview_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": plan_pro["id"], "as_of": as_of_dt.isoformat()},
    )
    assert preview_res.status_code == 200
    data = preview_res.json()

    assert data["total_billing_days"] == 30
    assert data["remaining_billing_days"] == 15
    assert Decimal(data["unused_credit"]) == Decimal("1000.00")
    assert Decimal(data["prorated_charge"]) == Decimal("500.00")
    assert Decimal(data["net_amount_due"]) == Decimal("-500.00")
    assert Decimal(data["total_due"]) == Decimal("0.00")
    assert Decimal(data["credit_carried_forward"]) == Decimal("500.00")
    assert data["change_type"] == "downgrade"


def test_proration_preview_zero_remaining_days(api_client, db_session):
    """Validates that when as_of is on or past current_period_end, remaining days is 0."""
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_pro["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    start_dt = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    as_of_dt = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)  # at end date

    sub_obj = db_session.get(Subscription, sub_id)
    sub_obj.current_period_start = start_dt
    sub_obj.current_period_end = end_dt
    db_session.commit()

    preview_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": plan_prem["id"], "as_of": as_of_dt.isoformat()},
    )
    assert preview_res.status_code == 200
    data = preview_res.json()

    assert data["remaining_billing_days"] == 0
    assert Decimal(data["unused_credit"]) == Decimal("0.00")
    assert Decimal(data["prorated_charge"]) == Decimal("0.00")
    assert Decimal(data["net_amount_due"]) == Decimal("0.00")
    assert Decimal(data["total_due"]) == Decimal("0.00")


def test_proration_preview_error_cases(api_client, db_session):
    """Validates rejection of same-plan, currency mismatch, archived plans, and invalid statuses."""
    cust, plan_pro, plan_prem, _, plan_usd = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_pro["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    # 1. Same plan rejection
    same_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": plan_pro["id"]},
    )
    assert same_res.status_code == 400
    assert "already assigned" in same_res.json()["detail"].lower()

    # 2. Currency mismatch rejection (INR vs USD)
    curr_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": plan_usd["id"]},
    )
    assert curr_res.status_code == 400
    assert "currency" in curr_res.json()["detail"].lower()

    # 3. Nonexistent subscription
    assert api_client.post(
        "/api/v1/subscriptions/99999/proration-preview",
        json={"new_plan_id": plan_prem["id"]},
    ).status_code == 404

    # 4. Nonexistent plan
    assert api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": 99999},
    ).status_code == 404

    # 5. Archived plan
    arch_res = api_client.delete(f"/api/v1/plans/{plan_prem['id']}")
    assert arch_res.status_code in (200, 204)
    arch_preview = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": plan_prem["id"]},
    )
    assert arch_preview.status_code == 400


def test_confirmed_plan_change_creates_proration_invoice(api_client, db_session):
    """
    Executes a confirmed plan change upgrade and verifies:
    1. Subscription snapshot updated to new plan
    2. Adjustment invoice generated with PLAN_UPGRADE and PRORATION_CREDIT line items
    3. Invoice totals match: subtotal ₹1000, discount ₹500, total ₹500
    4. Subscription and invoice audit logs recorded
    """
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_pro["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    start_dt = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    as_of_dt = datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)

    sub_obj = db_session.get(Subscription, sub_id)
    sub_obj.current_period_start = start_dt
    sub_obj.current_period_end = end_dt
    db_session.commit()

    # Execute confirmed plan change
    change_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": plan_prem["id"], "as_of": as_of_dt.isoformat()},
    )
    assert change_res.status_code == 200
    res_data = change_res.json()

    assert res_data["plan_id"] == plan_prem["id"]
    assert Decimal(res_data["unit_price"]) == Decimal("2000.00")
    assert res_data["invoice_id"] is not None
    assert res_data["invoice_number"].startswith("INV-")
    assert Decimal(res_data["proration_net_amount"]) == Decimal("500.00")

    # Fetch and inspect the generated invoice from the database
    inv = db_session.get(Invoice, res_data["invoice_id"])
    assert inv is not None
    assert inv.subscription_id == sub_id
    assert inv.customer_id == cust["id"]
    assert inv.subtotal == Decimal("1000.00")
    assert inv.discount_amount == Decimal("500.00")
    assert inv.total_amount == Decimal("500.00")
    assert inv.status == InvoiceStatus.OPEN

    # Verify line items
    items_by_type = {item.item_type: item for item in inv.items}
    assert InvoiceItemType.PLAN_UPGRADE in items_by_type
    assert InvoiceItemType.PRORATION_CREDIT in items_by_type

    upgrade_item = items_by_type[InvoiceItemType.PLAN_UPGRADE]
    assert upgrade_item.amount == Decimal("1000.00")
    assert "15/30" in upgrade_item.description

    credit_item = items_by_type[InvoiceItemType.PRORATION_CREDIT]
    assert credit_item.amount == Decimal("500.00")
    assert "15/30" in credit_item.description

    # Verify AuditLogs recorded
    sub_logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == sub_id)
        .all()
    )
    assert any("upgraded" in log.description.lower() for log in sub_logs)


def test_confirmed_downgrade_creates_paid_adjustment_invoice(api_client, db_session):
    """
    Executes a confirmed downgrade and verifies:
    1. Adjustment invoice generated with total_amount = ₹0.00
    2. Status is immediately PAID since customer owes nothing
    """
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_prem["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    start_dt = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    as_of_dt = datetime(2026, 6, 16, 0, 0, 0, tzinfo=timezone.utc)

    sub_obj = db_session.get(Subscription, sub_id)
    sub_obj.current_period_start = start_dt
    sub_obj.current_period_end = end_dt
    db_session.commit()

    change_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": plan_pro["id"], "as_of": as_of_dt.isoformat()},
    )
    assert change_res.status_code == 200
    res_data = change_res.json()

    inv = db_session.get(Invoice, res_data["invoice_id"])
    assert inv.total_amount == Decimal("0.00")
    assert inv.status == InvoiceStatus.PAID
    assert inv.paid_at is not None

    items_by_type = {item.item_type: item for item in inv.items}
    assert InvoiceItemType.PRORATION_DEBIT in items_by_type
    assert InvoiceItemType.PRORATION_CREDIT in items_by_type


def test_subscription_history_timeline(api_client, db_session):
    """
    Verifies that GET /api/v1/subscriptions/{id}/history returns a chronological
    timeline of lifecycle events and proration invoices with actual backend data.
    """
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_pro["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    # Execute an upgrade
    change_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": plan_prem["id"]},
    )
    assert change_res.status_code == 200

    # Fetch history timeline
    hist_res = api_client.get(f"/api/v1/subscriptions/{sub_id}/history")
    assert hist_res.status_code == 200
    timeline = hist_res.json()

    assert len(timeline) >= 2  # At least: subscription_created, plan_changed, and proration invoice
    event_types = [e["event_type"] for e in timeline]
    assert "plan_changed" in event_types
    assert any("invoice" in et for et in event_types)


def test_decimal_rounding_half_up_precision(api_client, db_session):
    """
    Verifies that proration handles irregular day counts and decimal pricing
    with strict ROUND_HALF_UP rounding and zero float error.
    """
    cust, _, _, _, _ = _setup_proration_fixtures(api_client)

    uid = uuid.uuid4().hex[:8]
    p1 = api_client.post(
        "/api/v1/plans",
        json={
            "code": f"PREC1-{uid}",
            "name": "Precision Plan 1",
            "price": "99.99",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    p2 = api_client.post(
        "/api/v1/plans",
        json={
            "code": f"PREC2-{uid}",
            "name": "Precision Plan 2",
            "price": "149.95",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": p1["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    # Period: Jan 1 to Feb 1 = 31 days. Remaining: Jan 20 to Feb 1 = 12 days.
    start_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
    as_of_dt = datetime(2026, 1, 20, 0, 0, 0, tzinfo=timezone.utc)

    sub_obj = db_session.get(Subscription, sub_id)
    sub_obj.current_period_start = start_dt
    sub_obj.current_period_end = end_dt
    db_session.commit()

    preview_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/proration-preview",
        json={"new_plan_id": p2["id"], "as_of": as_of_dt.isoformat()},
    )
    assert preview_res.status_code == 200
    data = preview_res.json()

    assert data["total_billing_days"] == 31
    assert data["remaining_billing_days"] == 12

    # Expected:
    # unused_credit = 99.99 * 12 / 31 = 38.7058... -> 38.71
    # prorated_charge = 149.95 * 12 / 31 = 58.0451... -> 58.05
    # net_due = 58.05 - 38.71 = 19.34
    assert Decimal(data["unused_credit"]) == Decimal("38.71")
    assert Decimal(data["prorated_charge"]) == Decimal("58.05")
    assert Decimal(data["net_amount_due"]) == Decimal("19.34")
    assert Decimal(data["total_due"]) == Decimal("19.34")


def test_plan_change_transaction_safety_rollback(api_client, db_session, monkeypatch):
    """
    Verifies transaction safety: if an unexpected failure occurs during adjustment invoice
    generation or audit logging, the entire transaction rolls back atomically.
    Subscription unit_price, plan_id, and records remain completely unmutated.
    """
    cust, plan_pro, plan_prem, _, _ = _setup_proration_fixtures(api_client)

    sub_res = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan_pro["id"], "status": "active"},
    )
    sub_id = sub_res.json()["id"]

    sub_obj = db_session.get(Subscription, sub_id)
    original_price = sub_obj.unit_price
    original_plan_id = sub_obj.plan_id

    # Simulate an error during invoice creation
    from app.services.invoice_service import InvoiceService
    def _exploding_create_invoice(*args, **kwargs):
        raise RuntimeError("Simulated Database I/O Failure during invoice generation!")

    monkeypatch.setattr(InvoiceService, "create_itemized_invoice", _exploding_create_invoice)

    with pytest.raises(RuntimeError, match="Simulated Database I/O Failure"):
        from app.schemas.subscription import SubscriptionPlanChangeRequest
        SubscriptionService.change_plan(
            db_session,
            sub_id,
            SubscriptionPlanChangeRequest(new_plan_id=plan_prem["id"]),
        )

    # Rollback session and verify subscription is unchanged
    db_session.rollback()
    refreshed_sub = db_session.get(Subscription, sub_id)
    assert refreshed_sub.unit_price == original_price
    assert refreshed_sub.plan_id == original_plan_id

    # Verify no invoices were committed
    invoices = db_session.query(Invoice).filter(Invoice.subscription_id == sub_id).all()
    assert len(invoices) == 0

