"""
End-to-End Integration Tests for Phase 3 Plan Management APIs.
Executes strictly against subscription_billing_test_db.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.models import AuditLog, Customer, Plan, Subscription, SubscriptionStatus
from app.services.plan_service import PlanArchivedError, PlanService


def test_create_monthly_and_annual_plans(api_client, db_session):
    assert db_session.bind.url.database == "subscription_billing_test_db"

    # 1. Create Monthly Plan with trial and feature list
    monthly_payload = {
        "name": "Starter Monthly",
        "code": "starter-mo",
        "description": "Entry tier for startups",
        "price": "29.00",
        "currency": "usd",
        "billing_interval": "monthly",
        "trial_days": 14,
        "features": ["Up to 100 Subscribers", "Automated Invoicing"],
    }
    res_mo = api_client.post("/api/v1/plans", json=monthly_payload)
    assert res_mo.status_code == 201
    mo_data = res_mo.json()
    assert mo_data["code"] == "STARTER-MO"
    assert mo_data["currency"] == "USD"
    assert mo_data["billing_interval"] == "monthly"
    assert mo_data["trial_days"] == 14
    assert Decimal(mo_data["price"]) == Decimal("29.00")
    assert mo_data["features"] == ["Up to 100 Subscribers", "Automated Invoicing"]

    # 2. Create Annual Plan with dict entitlements
    annual_payload = {
        "name": "Enterprise Annual",
        "code": "ENT-YEARLY",
        "description": "Annual enterprise license",
        "price": "1999.99",
        "currency": "USD",
        "billing_interval": "yearly",
        "trial_days": 30,
        "features": {"max_seats": 500, "sla": "99.99%", "pdf_invoices": True},
    }
    res_yr = api_client.post("/api/v1/plans", json=annual_payload)
    assert res_yr.status_code == 201
    yr_data = res_yr.json()
    assert yr_data["billing_interval"] == "yearly"
    assert yr_data["features"]["sla"] == "99.99%"

    # Verify AuditLog records persisted
    logs = db_session.query(AuditLog).filter(AuditLog.entity_type == "plan").all()
    assert len(logs) == 2
    assert {log.action for log in logs} == {"plan.created"}


def test_duplicate_plan_code_rejected(api_client):
    payload = {
        "name": "Pro Monthly",
        "code": "PRO-MO",
        "price": "79.00",
        "billing_interval": "monthly",
    }
    first = api_client.post("/api/v1/plans", json=payload)
    assert first.status_code == 201

    # Attempt duplicate (even lowercase normalizes to PRO-MO)
    dup_payload = {**payload, "name": "Pro Duplicate", "code": "pro-mo"}
    second = api_client.post("/api/v1/plans", json=dup_payload)
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


def test_invalid_pricing_and_validation_rejected(api_client):
    # Negative price
    res_neg = api_client.post(
        "/api/v1/plans",
        json={"name": "Invalid", "code": "BAD-PRICE", "price": "-5.00"},
    )
    assert res_neg.status_code == 422

    # Negative trial days
    res_trial = api_client.post(
        "/api/v1/plans",
        json={"name": "Invalid Trial", "code": "BAD-TRIAL", "price": "10.00", "trial_days": -1},
    )
    assert res_trial.status_code == 422

    # Invalid currency
    res_curr = api_client.post(
        "/api/v1/plans",
        json={"name": "Invalid Currency", "code": "BAD-CURR", "price": "10.00", "currency": "US"},
    )
    assert res_curr.status_code == 422


def test_list_plans_pagination_and_filtering(api_client):
    for i in range(1, 5):
        res = api_client.post(
            "/api/v1/plans",
            json={
                "name": f"Monthly Tier {i}",
                "code": f"MO-{i}",
                "price": f"{i * 10}.00",
                "billing_interval": "monthly",
            },
        )
        assert res.status_code == 201

    res_yr = api_client.post(
        "/api/v1/plans",
        json={
            "name": "Annual Tier",
            "code": "YR-1",
            "price": "500.00",
            "billing_interval": "yearly",
        },
    )
    assert res_yr.status_code == 201

    # Page 1, size 2
    p1 = api_client.get("/api/v1/plans?page=1&size=2")
    assert p1.status_code == 200
    d1 = p1.json()
    assert d1["total"] == 5
    assert d1["page"] == 1
    assert d1["size"] == 2
    assert d1["pages"] == 3
    assert len(d1["items"]) == 2

    # Page 3, size 2
    p3 = api_client.get("/api/v1/plans?page=3&size=2")
    assert p3.status_code == 200
    assert len(p3.json()["items"]) == 1

    # Filter by billing_interval=yearly
    yr_list = api_client.get("/api/v1/plans?billing_interval=yearly")
    assert yr_list.status_code == 200
    assert yr_list.json()["total"] == 1
    assert yr_list.json()["items"][0]["code"] == "YR-1"


def test_get_plan_by_id_and_not_found(api_client):
    created = api_client.post(
        "/api/v1/plans",
        json={"name": "Basic", "code": "BASIC", "price": "15.00"},
    ).json()

    found = api_client.get(f"/api/v1/plans/{created['id']}")
    assert found.status_code == 200
    assert found.json()["code"] == "BASIC"

    missing = api_client.get("/api/v1/plans/999999")
    assert missing.status_code == 404


def test_update_plan_preserves_existing_subscription_historical_pricing(api_client, db_session):
    # 1. Create plan at $29.00
    plan_res = api_client.post(
        "/api/v1/plans",
        json={"name": "Growth", "code": "GROWTH", "price": "29.00", "currency": "USD", "billing_interval": "monthly"},
    )
    assert plan_res.status_code == 201
    plan_id = plan_res.json()["id"]

    # 2. Attach a customer & active subscription at $29.00
    now = datetime.now(timezone.utc)
    customer = Customer(customer_code="CUST-HIST-1", full_name="Kiran Patel", email="kiran@example.com")
    db_session.add(customer)
    db_session.flush()

    subscription = Subscription(
        subscription_code="SUB-HIST-1",
        customer_id=customer.id,
        plan_id=plan_id,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(subscription)
    db_session.commit()
    sub_id = subscription.id
    assert subscription.unit_price == Decimal("29.00")

    # 3. Update plan price to $59.00 via PUT API
    upd_res = api_client.put(
        f"/api/v1/plans/{plan_id}",
        json={"price": "59.00", "trial_days": 7, "features": ["Updated Feature"]},
    )
    assert upd_res.status_code == 200
    upd_data = upd_res.json()
    assert Decimal(upd_data["price"]) == Decimal("59.00")
    assert upd_data["trial_days"] == 7

    # 4. Verify existing subscription STILL holds historical $29.00 price
    db_session.expire_all()
    persisted_sub = db_session.get(Subscription, sub_id)
    assert persisted_sub.unit_price == Decimal("29.00")
    assert persisted_sub.plan.price == Decimal("59.00")

    # Reject negative price on update
    bad_upd = api_client.put(f"/api/v1/plans/{plan_id}", json={"price": "-10.00"})
    assert bad_upd.status_code == 422


def test_archive_plan_and_restrictions(api_client, db_session):
    # Create plan + historical subscription
    plan_data = api_client.post(
        "/api/v1/plans",
        json={"name": "Legacy Plan", "code": "LEGACY", "price": "19.00"},
    ).json()
    plan_id = plan_data["id"]

    now = datetime.now(timezone.utc)
    cust = Customer(customer_code="CUST-LEG", full_name="Legacy User", email="legacy@example.com")
    db_session.add(cust)
    db_session.flush()
    sub = Subscription(
        subscription_code="SUB-LEG",
        customer_id=cust.id,
        plan_id=plan_id,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(sub)
    db_session.commit()

    # Archive via PATCH
    arch_res = api_client.patch(f"/api/v1/plans/{plan_id}/archive")
    assert arch_res.status_code == 200
    arch_data = arch_res.json()
    assert arch_data["is_active"] is False
    assert arch_data["deleted_at"] is not None

    # Excluded from default GET /plans
    list_default = api_client.get("/api/v1/plans").json()
    assert list_default["total"] == 0

    # Visible only when include_archived=true
    list_all = api_client.get("/api/v1/plans?include_archived=true").json()
    assert list_all["total"] == 1
    assert list_all["items"][0]["id"] == plan_id

    # Double archive returns 409 Conflict
    double_arch = api_client.patch(f"/api/v1/plans/{plan_id}/archive")
    assert double_arch.status_code == 409

    # Updating archived plan returns 400 Bad Request
    upd_arch = api_client.put(f"/api/v1/plans/{plan_id}", json={"name": "Cant Edit"})
    assert upd_arch.status_code == 400

    # Business guard rejects archived plan for new subscriptions
    db_session.expire_all()
    with pytest.raises(PlanArchivedError):
        PlanService.ensure_plan_available_for_subscription(db_session, plan_id)

    # Historical subscription remains intact!
    assert db_session.get(Subscription, sub.id) is not None


def test_delete_plan_api_and_historical_preservation(api_client, db_session):
    """
    Verifies DELETE /api/v1/plans/{plan_id}:
    - Soft-deletes plans consistently with archive architecture.
    - Preserves historical subscriptions, price snapshots, and financial records.
    - Excludes deleted plans from active listings.
    - Handles nonexistent plans (404) and repeated deletion (409).
    - Prevents new subscriptions from referencing deleted plans.
    """
    # 1. Nonexistent plan returns 404
    missing_del = api_client.delete("/api/v1/plans/999999")
    assert missing_del.status_code == 404
    assert "not found" in missing_del.json()["detail"].lower()

    # 2. Create plan at $49.00
    plan_res = api_client.post(
        "/api/v1/plans",
        json={"name": "Pro Tier", "code": "PRO-DEL-TEST", "price": "49.00", "currency": "USD"},
    )
    assert plan_res.status_code == 201
    plan_id = plan_res.json()["id"]

    # 3. Create customer and attach active subscription with historical price snapshot
    now = datetime.now(timezone.utc)
    customer = Customer(customer_code="CUST-DEL-PLAN", full_name="Sub Plan Tester", email="sub.del@example.com")
    db_session.add(customer)
    db_session.flush()

    sub = Subscription(
        subscription_code="SUB-DEL-HIST",
        customer_id=customer.id,
        plan_id=plan_id,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db_session.add(sub)
    db_session.commit()
    sub_id = sub.id
    assert sub.unit_price == Decimal("49.00")

    # 4. Delete plan via DELETE /api/v1/plans/{plan_id}
    del_res = api_client.delete(f"/api/v1/plans/{plan_id}")
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["id"] == plan_id
    assert del_data["is_active"] is False
    assert del_data["deleted_at"] is not None

    # Verify audit log recorded "plan.deleted"
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "plan", AuditLog.entity_id == plan_id, AuditLog.action == "plan.deleted")
        .first()
    )
    assert audit is not None
    assert audit.action == "plan.deleted"

    # 5. Deleted plan is excluded from default list
    list_active = api_client.get("/api/v1/plans").json()
    assert not any(p["id"] == plan_id for p in list_active["items"])

    # Visible in include_archived=true
    list_all = api_client.get("/api/v1/plans?include_archived=true").json()
    matching = [p for p in list_all["items"] if p["id"] == plan_id]
    assert len(matching) == 1
    assert matching[0]["is_active"] is False
    assert matching[0]["deleted_at"] is not None

    # 6. Repeated deletion returns 409 Conflict
    repeat_del = api_client.delete(f"/api/v1/plans/{plan_id}")
    assert repeat_del.status_code == 409
    assert "already archived or deleted" in repeat_del.json()["detail"].lower()

    # 7. PATCH /archive on an already deleted plan also returns 409 Conflict
    patch_after_del = api_client.patch(f"/api/v1/plans/{plan_id}/archive")
    assert patch_after_del.status_code == 409

    # 8. Updating deleted plan returns 400 Bad Request
    upd_del = api_client.put(f"/api/v1/plans/{plan_id}", json={"name": "New Name"})
    assert upd_del.status_code == 400

    # 9. Existing historical subscription and price snapshot are strictly preserved
    db_session.expire_all()
    persisted_sub = db_session.get(Subscription, sub_id)
    assert persisted_sub is not None
    assert persisted_sub.plan_id == plan_id
    assert persisted_sub.unit_price == Decimal("49.00")
    assert persisted_sub.status == SubscriptionStatus.ACTIVE

    # 10. Attempting to create a NEW subscription for deleted plan is rejected
    cust2 = Customer(customer_code="CUST-DEL-2", full_name="Second User", email="second@example.com")
    db_session.add(cust2)
    db_session.commit()

    sub_fail = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust2.id, "plan_id": plan_id},
    )
    assert sub_fail.status_code == 400
    assert "archived or inactive" in sub_fail.json()["detail"].lower()


