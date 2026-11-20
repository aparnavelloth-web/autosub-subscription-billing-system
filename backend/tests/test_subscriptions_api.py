"""
End-to-End Integration Tests for Phase 3 Subscription Management APIs & State Machine.
Executes strictly against subscription_billing_test_db.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from app.models import AuditLog, Customer, Subscription, SubscriptionStatus
from app.schemas.subscription import SubscriptionPauseRequest
from app.services.subscription_service import (
    InvalidSubscriptionStateError,
    SubscriptionPauseConflictError,
    SubscriptionService,
)


def _create_customer_and_plans(api_client):
    cust = api_client.post(
        "/api/v1/customers",
        json={"full_name": "Vikram Mehta", "email": "vikram@example.com", "currency": "USD"},
    ).json()

    starter_trial = api_client.post(
        "/api/v1/plans",
        json={
            "name": "Starter Monthly",
            "code": "START-MO",
            "price": "29.00",
            "currency": "USD",
            "billing_interval": "monthly",
            "trial_days": 14,
        },
    ).json()

    pro_monthly = api_client.post(
        "/api/v1/plans",
        json={
            "name": "Pro Monthly",
            "code": "PRO-MO",
            "price": "79.00",
            "currency": "USD",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    ).json()

    ent_annual = api_client.post(
        "/api/v1/plans",
        json={
            "name": "Enterprise Annual",
            "code": "ENT-YR",
            "price": "799.00",
            "currency": "USD",
            "billing_interval": "yearly",
            "trial_days": 0,
        },
    ).json()

    return cust, starter_trial, pro_monthly, ent_annual


def test_create_trial_and_active_subscriptions_monthly_and_annual(api_client, db_session):
    assert db_session.bind.url.database == "subscription_billing_test_db"
    cust, starter_trial, _, ent_annual = _create_customer_and_plans(api_client)

    # 1. Plan with trial_days=14 defaults to 'trial' status
    res_trial = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": starter_trial["id"], "quantity": 2},
    )
    assert res_trial.status_code == 201
    t_data = res_trial.json()
    assert t_data["status"] == "trial"
    assert t_data["quantity"] == 2
    assert Decimal(t_data["unit_price"]) == Decimal("29.00")
    assert t_data["currency"] == "USD"
    assert t_data["billing_interval"] == "monthly"
    assert t_data["trial_start"] is not None
    assert t_data["trial_end"] is not None
    assert t_data["current_period_end"] == t_data["trial_end"]

    # 2. Annual plan with trial_days=0 defaults to 'active' status
    start_dt = "2026-03-01T00:00:00Z"
    res_annual = api_client.post(
        "/api/v1/subscriptions",
        json={
            "customer_id": cust["id"],
            "plan_id": ent_annual["id"],
            "start_date": start_dt,
        },
    )
    assert res_annual.status_code == 201
    a_data = res_annual.json()
    assert a_data["status"] == "active"
    assert a_data["billing_interval"] == "yearly"
    assert Decimal(a_data["unit_price"]) == Decimal("799.00")
    assert a_data["trial_start"] is None
    assert a_data["current_period_end"].startswith("2027-03-01")

    logs = db_session.query(AuditLog).filter(AuditLog.entity_type == "subscription").all()
    assert len(logs) == 2
    assert {l.action for l in logs} == {"subscription.created"}


def test_subscription_rejects_archived_plan_and_deleted_customer(api_client, db_session):
    cust, starter_trial, pro_monthly, _ = _create_customer_and_plans(api_client)

    # Archive pro_monthly plan
    arch = api_client.patch(f"/api/v1/plans/{pro_monthly['id']}/archive")
    assert arch.status_code == 200

    res_arch = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    )
    assert res_arch.status_code == 400

    # Soft-delete customer
    c_obj = db_session.get(Customer, cust["id"])
    c_obj.soft_delete()
    db_session.commit()

    res_del_cust = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": starter_trial["id"]},
    )
    assert res_del_cust.status_code == 400

    # Non-existent customer or plan -> 404
    assert api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": 999999, "plan_id": starter_trial["id"]},
    ).status_code == 404


def test_historical_pricing_preserved_across_plan_price_changes(api_client):
    cust, _, pro_monthly, _ = _create_customer_and_plans(api_client)

    sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    assert Decimal(sub["unit_price"]) == Decimal("79.00")

    # Change Pro Monthly price from $79 -> $119
    upd_plan = api_client.put(
        f"/api/v1/plans/{pro_monthly['id']}",
        json={"price": "119.00"},
    )
    assert upd_plan.status_code == 200

    # Subscription keeps historical $79.00 price
    fetched_sub = api_client.get(f"/api/v1/subscriptions/{sub['id']}").json()
    assert Decimal(fetched_sub["unit_price"]) == Decimal("79.00")


def test_plan_upgrade_and_downgrade(api_client, db_session):
    cust, starter_trial, pro_monthly, ent_annual = _create_customer_and_plans(api_client)

    sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": starter_trial["id"], "status": "active"},
    ).json()
    sub_id = sub["id"]
    assert Decimal(sub["unit_price"]) == Decimal("29.00")

    # Upgrade Starter ($29) -> Pro ($79)
    up_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": pro_monthly["id"], "quantity": 3},
    )
    assert up_res.status_code == 200
    up_data = up_res.json()
    assert up_data["plan_id"] == pro_monthly["id"]
    assert Decimal(up_data["unit_price"]) == Decimal("79.00")
    assert up_data["quantity"] == 3

    # Downgrade Pro ($79) -> Starter ($29)
    down_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": starter_trial["id"]},
    )
    assert down_res.status_code == 200
    assert Decimal(down_res.json()["unit_price"]) == Decimal("29.00")

    # Upgrade to Annual with reset_billing_period=True
    yr_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": ent_annual["id"], "reset_billing_period": True},
    )
    assert yr_res.status_code == 200
    assert yr_res.json()["billing_interval"] == "yearly"
    assert Decimal(yr_res.json()["unit_price"]) == Decimal("799.00")

    # Reject same plan no-op
    same_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": ent_annual["id"]},
    )
    assert same_res.status_code == 400

    # Verify AuditLogs captured upgrade/downgrade descriptions
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == sub_id)
        .order_by(AuditLog.id.asc())
        .all()
    )
    assert len(logs) == 4  # created + 3 plan changes
    assert "upgraded" in logs[1].description
    assert "downgraded" in logs[2].description
    assert "upgraded" in logs[3].description


def test_subscription_state_machine_valid_and_invalid_transitions(api_client, db_session):
    cust, starter_trial, _, _ = _create_customer_and_plans(api_client)

    # Start in TRIAL
    sub1 = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": starter_trial["id"]},
    ).json()
    sub1_id = sub1["id"]
    assert sub1["status"] == "trial"

    # Invalid: TRIAL -> PAST_DUE
    with pytest.raises(InvalidSubscriptionStateError):
        SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.PAST_DUE)

    # Valid: TRIAL -> ACTIVE
    s = SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.ACTIVE)
    assert s.status == SubscriptionStatus.ACTIVE

    # Invalid: ACTIVE -> TRIAL
    with pytest.raises(InvalidSubscriptionStateError):
        SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.TRIAL)

    # Valid: ACTIVE -> PAST_DUE
    s = SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.PAST_DUE)
    assert s.status == SubscriptionStatus.PAST_DUE

    # Valid recovery: PAST_DUE -> ACTIVE
    s = SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.ACTIVE)
    assert s.status == SubscriptionStatus.ACTIVE

    # Valid: ACTIVE -> PAST_DUE -> CANCELLED
    SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.PAST_DUE)
    s = SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.CANCELLED)
    assert s.status == SubscriptionStatus.CANCELLED
    assert s.cancelled_at is not None

    # Invalid: CANCELLED -> ACTIVE (terminal state)
    with pytest.raises(InvalidSubscriptionStateError):
        SubscriptionService.transition_status(db_session, sub1_id, SubscriptionStatus.ACTIVE)

    # Valid: TRIAL -> CANCELLED
    sub2 = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": starter_trial["id"]},
    ).json()
    s2 = SubscriptionService.transition_status(db_session, sub2["id"], SubscriptionStatus.CANCELLED)
    assert s2.status == SubscriptionStatus.CANCELLED


def test_immediate_and_end_of_cycle_cancellation(api_client, db_session):
    cust, _, pro_monthly, _ = _create_customer_and_plans(api_client)
    sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    sub_id = sub["id"]

    # 1. End-of-cycle cancellation
    eoc_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/cancel",
        json={"immediate": False, "reason": "Scheduled non-renewal"},
    )
    assert eoc_res.status_code == 200
    eoc_data = eoc_res.json()
    assert eoc_data["status"] == "active"
    assert eoc_data["cancel_at_period_end"] is True
    assert eoc_data["cancelled_at"] is None

    # Duplicate end-of-cycle request rejected
    assert api_client.post(
        f"/api/v1/subscriptions/{sub_id}/cancel",
        json={"immediate": False},
    ).status_code == 400

    # 2. Immediate cancellation
    imm_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/cancel",
        json={"immediate": True, "reason": "Customer requested immediate stop"},
    )
    assert imm_res.status_code == 200
    imm_data = imm_res.json()
    assert imm_data["status"] == "cancelled"
    assert imm_data["cancel_at_period_end"] is False
    assert imm_data["cancelled_at"] is not None

    # Cancelling already cancelled subscription -> 400
    assert api_client.post(
        f"/api/v1/subscriptions/{sub_id}/cancel",
        json={"immediate": True},
    ).status_code == 400

    # Plan change on cancelled subscription -> 400
    assert api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": pro_monthly["id"]},
    ).status_code == 400

    # Verify audit logs recorded cancel_scheduled and cancelled
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == sub_id)
        .order_by(AuditLog.id.asc())
        .all()
    )
    assert [l.action for l in logs] == [
        "subscription.created",
        "subscription.cancel_scheduled",
        "subscription.cancelled",
    ]


def test_list_subscriptions_pagination_and_filters(api_client):
    cust, starter_trial, pro_monthly, _ = _create_customer_and_plans(api_client)
    api_client.post("/api/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": starter_trial["id"]})
    api_client.post("/api/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]})
    api_client.post("/api/v1/subscriptions", json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]})

    all_subs = api_client.get("/api/v1/subscriptions?page=1&size=2")
    assert all_subs.status_code == 200
    assert all_subs.json()["total"] == 3
    assert all_subs.json()["pages"] == 2
    assert len(all_subs.json()["items"]) == 2

    trial_subs = api_client.get("/api/v1/subscriptions?status=trial")
    assert trial_subs.status_code == 200
    assert trial_subs.json()["total"] == 1

    active_subs = api_client.get(f"/api/v1/subscriptions?status=active&plan_id={pro_monthly['id']}")
    assert active_subs.status_code == 200
    assert active_subs.json()["total"] == 2


def test_successful_pause_resume_and_remaining_time_preservation(api_client, db_session):
    cust, starter_trial, pro_monthly, _ = _create_customer_and_plans(api_client)
    sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    sub_id = sub["id"]
    assert sub["is_paused"] is False

    # 1. Pause active paid subscription
    pause_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/pause",
        json={"reason": "Seasonal break"},
    )
    assert pause_res.status_code == 200
    p_data = pause_res.json()
    assert p_data["status"] == "active"  # SubscriptionStatus enum preserved!
    assert p_data["is_paused"] is True
    assert p_data["paused_at"] is not None
    assert p_data["pause_reason"] == "Seasonal break"
    assert p_data["paused_remaining_seconds"] > 2400000

    # Paused subscription excluded from billable query even far in the future
    db_session.expire_all()
    sub_obj = db_session.get(Subscription, sub_id)
    assert sub_obj.is_billable is False
    billable = SubscriptionService.list_billable_subscriptions(
        db_session, as_of=datetime.now(timezone.utc) + timedelta(days=365)
    )
    assert all(b.id != sub_id for b in billable)

    # Plan change rejected while paused
    assert api_client.post(
        f"/api/v1/subscriptions/{sub_id}/change-plan",
        json={"new_plan_id": starter_trial["id"]},
    ).status_code == 400

    # Simulate 20 days passing while paused (with 15 days prepaid remaining stored)
    now = datetime.now(timezone.utc)
    sub_obj.current_period_start = now - timedelta(days=35)
    sub_obj.current_period_end = now - timedelta(days=5)
    sub_obj.paused_at = now - timedelta(days=20)
    sub_obj.paused_remaining_seconds = 15 * 86400
    db_session.commit()

    # 2. Resume subscription -> renewal date shifted forward by exact 15 days!
    resume_res = api_client.post(
        f"/api/v1/subscriptions/{sub_id}/resume",
        json={"reason": "Customer resumed"},
    )
    assert resume_res.status_code == 200
    r_data = resume_res.json()
    assert r_data["status"] == "active"
    assert r_data["is_paused"] is False
    assert r_data["resumed_at"] is not None
    assert r_data["paused_remaining_seconds"] is None
    assert r_data["cancelled_at"] is None
    assert r_data["cancel_at_period_end"] is False

    resumed_at_dt = datetime.fromisoformat(r_data["resumed_at"])
    new_end_dt = datetime.fromisoformat(r_data["current_period_end"])
    assert abs((new_end_dt - resumed_at_dt).total_seconds() - (15 * 86400)) < 2

    # Verify billable check re-enabled once resumed
    db_session.expire_all()
    assert db_session.get(Subscription, sub_id).is_billable is True

    # Verify AuditLogs recorded pause and resume
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "subscription", AuditLog.entity_id == sub_id)
        .order_by(AuditLog.id.asc())
        .all()
    )
    assert [l.action for l in logs] == [
        "subscription.created",
        "subscription.paused",
        "subscription.resumed",
    ]
    assert logs[1].old_values["is_paused"] is False
    assert logs[1].new_values["is_paused"] is True
    assert logs[2].old_values["is_paused"] is True
    assert logs[2].new_values["is_paused"] is False


def test_invalid_and_repeated_pause_resume_operations(api_client, db_session):
    cust, starter_trial, pro_monthly, _ = _create_customer_and_plans(api_client)

    # 1. Trial subscription cannot be paused
    trial_sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": starter_trial["id"]},
    ).json()
    assert api_client.post(f"/api/v1/subscriptions/{trial_sub['id']}/pause").status_code == 400
    assert api_client.post(f"/api/v1/subscriptions/{trial_sub['id']}/resume").status_code == 400

    # 2. Active subscription: resume when not paused -> 409; repeated pause -> 409; repeated resume -> 409
    active_sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    act_id = active_sub["id"]

    assert api_client.post(f"/api/v1/subscriptions/{act_id}/resume").status_code == 409
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/pause").status_code == 200
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/pause").status_code == 409
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/resume").status_code == 200
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/resume").status_code == 409

    # 3. Past-due subscription cannot be paused or resumed
    SubscriptionService.transition_status(db_session, act_id, SubscriptionStatus.PAST_DUE)
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/pause").status_code == 400
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/resume").status_code == 400

    # 4. Cancelled subscription cannot be paused or resumed
    SubscriptionService.transition_status(db_session, act_id, SubscriptionStatus.CANCELLED)
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/pause").status_code == 400
    assert api_client.post(f"/api/v1/subscriptions/{act_id}/resume").status_code == 400

    # 5. Missing subscription -> 404
    assert api_client.post("/api/v1/subscriptions/999999/pause").status_code == 404
    assert api_client.post("/api/v1/subscriptions/999999/resume").status_code == 404


def test_cancellation_while_paused_and_no_accidental_cancel_on_resume(api_client, db_session):
    cust, _, pro_monthly, _ = _create_customer_and_plans(api_client)

    # Case A: End-of-cycle cancel while paused, then resume -> stays active (not accidentally cancelled)
    sub_a = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    assert api_client.post(f"/api/v1/subscriptions/{sub_a['id']}/pause").status_code == 200
    eoc = api_client.post(f"/api/v1/subscriptions/{sub_a['id']}/cancel", json={"immediate": False})
    assert eoc.status_code == 200
    assert eoc.json()["cancel_at_period_end"] is True
    assert eoc.json()["is_paused"] is True

    resumed_a = api_client.post(f"/api/v1/subscriptions/{sub_a['id']}/resume")
    assert resumed_a.status_code == 200
    assert resumed_a.json()["status"] == "active"
    assert resumed_a.json()["cancelled_at"] is None
    assert resumed_a.json()["cancel_at_period_end"] is True

    # Case B: Immediate cancel while paused -> permanently cancelled, can never resume
    sub_b = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    assert api_client.post(f"/api/v1/subscriptions/{sub_b['id']}/pause").status_code == 200
    imm = api_client.post(f"/api/v1/subscriptions/{sub_b['id']}/cancel", json={"immediate": True})
    assert imm.status_code == 200
    assert imm.json()["status"] == "cancelled"
    assert imm.json()["is_paused"] is False
    assert imm.json()["paused_remaining_seconds"] is None
    assert imm.json()["cancelled_at"] is not None

    # Cancelled subscription must never resume
    assert api_client.post(f"/api/v1/subscriptions/{sub_b['id']}/resume").status_code == 400


def test_concurrent_pause_locking_safety(api_client, session_factory):
    """
    Verifies PostgreSQL SELECT ... FOR UPDATE row locking prevents concurrent
    pause requests from corrupting state or creating duplicate pause audits.
    """
    cust, _, pro_monthly, _ = _create_customer_and_plans(api_client)
    sub = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": pro_monthly["id"]},
    ).json()
    sub_id = sub["id"]

    def _attempt_pause():
        db = session_factory()
        try:
            SubscriptionService.pause_subscription(
                db, sub_id, SubscriptionPauseRequest(reason="Concurrent test")
            )
            return "paused"
        except SubscriptionPauseConflictError:
            return "conflict"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _attempt_pause(), range(2)))

    assert sorted(results) == ["conflict", "paused"]

    db_check = session_factory()
    try:
        pause_logs = (
            db_check.query(AuditLog)
            .filter(
                AuditLog.entity_type == "subscription",
                AuditLog.entity_id == sub_id,
                AuditLog.action == "subscription.paused",
            )
            .all()
        )
        assert len(pause_logs) == 1
    finally:
        db_check.close()

