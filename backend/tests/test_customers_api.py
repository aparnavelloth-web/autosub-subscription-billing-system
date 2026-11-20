"""
End-to-End Integration Tests for Phase 3 Customer Management APIs.
Executes strictly against subscription_billing_test_db.
"""
from app.models import AuditLog, Customer


def test_create_customer_and_email_normalization(api_client, db_session):
    assert db_session.bind.url.database == "subscription_billing_test_db"

    payload = {
        "full_name": "  Rohan Deshmukh  ",
        "email": "  Rohan.Deshmukh@Example.COM ",
        "phone": "+91-9988776655",
        "company_name": "FinEdge Solutions",
        "billing_address": "Pune, Maharashtra, India",
        "tax_id": "27AAAAA0000A1Z5",
        "currency": "inr",
    }
    res = api_client.post("/api/v1/customers", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["full_name"] == "Rohan Deshmukh"
    assert data["email"] == "rohan.deshmukh@example.com"
    assert data["currency"] == "INR"
    assert data["customer_code"].startswith("CUST-")
    assert data["billing_address"] == "Pune, Maharashtra, India"

    # Explicit customer_code
    res2 = api_client.post(
        "/api/v1/customers",
        json={
            "customer_code": "cust-vip-01",
            "full_name": "Meera Iyer",
            "email": "meera@example.com",
            "currency": "USD",
        },
    )
    assert res2.status_code == 201
    assert res2.json()["customer_code"] == "CUST-VIP-01"

    logs = db_session.query(AuditLog).filter(AuditLog.entity_type == "customer").all()
    assert len(logs) == 2
    assert {l.action for l in logs} == {"customer.created"}


def test_duplicate_email_and_code_rejection(api_client):
    first = api_client.post(
        "/api/v1/customers",
        json={"customer_code": "C-DUP-1", "full_name": "User One", "email": "dup@example.com"},
    )
    assert first.status_code == 201

    # Duplicate email (different casing)
    dup_email = api_client.post(
        "/api/v1/customers",
        json={"customer_code": "C-DUP-2", "full_name": "User Two", "email": "DUP@Example.com"},
    )
    assert dup_email.status_code == 409

    # Duplicate customer code
    dup_code = api_client.post(
        "/api/v1/customers",
        json={"customer_code": "c-dup-1", "full_name": "User Three", "email": "other@example.com"},
    )
    assert dup_code.status_code == 409


def test_invalid_email_rejected(api_client):
    res = api_client.post(
        "/api/v1/customers",
        json={"full_name": "Invalid Email", "email": "not-an-email"},
    )
    assert res.status_code == 422


def test_list_customers_pagination_and_soft_deletion(api_client, db_session):
    created_ids = []
    for i in range(1, 5):
        res = api_client.post(
            "/api/v1/customers",
            json={"full_name": f"Client {i}", "email": f"client{i}@example.com"},
        )
        assert res.status_code == 201
        created_ids.append(res.json()["id"])

    # Soft-delete the 4th customer directly to verify API respects soft deletion
    cust4 = db_session.get(Customer, created_ids[3])
    cust4.soft_delete()
    db_session.commit()

    # Default listing excludes soft-deleted
    page1 = api_client.get("/api/v1/customers?page=1&size=2")
    assert page1.status_code == 200
    d1 = page1.json()
    assert d1["total"] == 3
    assert d1["pages"] == 2
    assert len(d1["items"]) == 2

    # include_deleted=true shows all 4
    all_res = api_client.get("/api/v1/customers?include_deleted=true")
    assert all_res.status_code == 200
    assert all_res.json()["total"] == 4

    # Attempting to update a soft-deleted customer returns 400
    upd_deleted = api_client.put(
        f"/api/v1/customers/{created_ids[3]}",
        json={"full_name": "Should Fail"},
    )
    assert upd_deleted.status_code == 400


def test_get_and_update_customer_audit_log(api_client, db_session):
    created = api_client.post(
        "/api/v1/customers",
        json={"full_name": "Ananya Roy", "email": "ananya@example.com", "currency": "USD"},
    ).json()
    cid = created["id"]

    get_res = api_client.get(f"/api/v1/customers/{cid}")
    assert get_res.status_code == 200
    assert get_res.json()["email"] == "ananya@example.com"

    assert api_client.get("/api/v1/customers/999999").status_code == 404

    upd_res = api_client.put(
        f"/api/v1/customers/{cid}",
        json={"billing_address": "Mumbai, India", "currency": "INR"},
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["billing_address"] == "Mumbai, India"
    assert upd_res.json()["currency"] == "INR"

    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "customer", AuditLog.entity_id == cid)
        .order_by(AuditLog.id.asc())
        .all()
    )
    assert len(logs) == 2
    assert logs[1].action == "customer.updated"
    assert logs[1].old_values["currency"] == "USD"
    assert logs[1].new_values["currency"] == "INR"


def test_list_customer_subscriptions(api_client):
    cust = api_client.post(
        "/api/v1/customers",
        json={"full_name": "Sub Holder", "email": "subholder@example.com"},
    ).json()
    plan = api_client.post(
        "/api/v1/plans",
        json={"name": "Pro", "code": "PRO-CS", "price": "50.00"},
    ).json()

    sub1 = api_client.post(
        "/api/v1/subscriptions",
        json={"customer_id": cust["id"], "plan_id": plan["id"]},
    )
    assert sub1.status_code == 201

    sub_list = api_client.get(f"/api/v1/customers/{cust['id']}/subscriptions")
    assert sub_list.status_code == 200
    assert sub_list.json()["total"] == 1
    assert sub_list.json()["items"][0]["customer_id"] == cust["id"]

    assert api_client.get("/api/v1/customers/999999/subscriptions").status_code == 404

