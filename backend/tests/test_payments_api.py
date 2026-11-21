"""
Stage 2 Integration & Concurrency Tests for MockPaymentGateway, PaymentService,
Payment Schemas, Idempotency Keys, Audit Logging, Transaction Rollback, and Payment APIs.
Executes strictly against subscription_billing_test_db.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
from unittest.mock import patch
import pytest
from app.models import AuditLog, Customer, Invoice, InvoiceStatus, Payment, PaymentMethod, PaymentStatus
from app.schemas.payment import PaymentInitiateRequest, PaymentProcessRequest
from app.services.invoice_service import InvoiceNotFoundError
from app.services.mock_payment_gateway import MockPaymentGateway, MockPaymentOutcome
from app.services.payment_service import (
    DuplicatePaymentError,
    InvalidPaymentOperationError,
    PaymentService,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def _seed_customer_and_invoice(
    db,
    *,
    total_amount: Decimal = Decimal("100.00"),
    currency: str = "USD",
    status: InvoiceStatus = InvoiceStatus.OPEN,
    amount_paid: Decimal = Decimal("0.00"),
):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"PCUST-{tag}",
        full_name=f"Pay User {tag}",
        email=f"pay.user.{tag.lower()}@example.com",
        currency=currency,
    )
    db.add(customer)
    db.flush()

    invoice = Invoice(
        invoice_number=f"INV-PAY-{tag}",
        customer_id=customer.id,
        subtotal=total_amount,
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=total_amount,
        amount_paid=amount_paid,
        currency=currency,
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


def test_mock_payment_gateway_simulated_outcomes():
    ok = MockPaymentGateway.charge(
        amount=Decimal("49.99"),
        currency="USD",
        payment_method=PaymentMethod.CREDIT_CARD,
        outcome=MockPaymentOutcome.SUCCEED,
    )
    assert ok.status == PaymentStatus.SUCCEEDED
    assert ok.transaction_reference.startswith("TXN-")
    assert ok.processed_at is not None
    assert ok.failure_reason is None
    assert ok.gateway_response["simulated"] is True
    assert ok.gateway_response["outcome"] == "succeeded"

    fail = MockPaymentGateway.charge(
        amount=Decimal("49.99"),
        currency="USD",
        outcome=MockPaymentOutcome.FAIL,
        failure_reason="Insufficient simulated balance",
    )
    assert fail.status == PaymentStatus.FAILED
    assert fail.processed_at is None
    assert fail.failure_reason == "Insufficient simulated balance"
    assert fail.gateway_response["outcome"] == "failed"

    pend = MockPaymentGateway.charge(
        amount=Decimal("49.99"),
        currency="USD",
        outcome=MockPaymentOutcome.PENDING,
    )
    assert pend.status == PaymentStatus.PENDING
    assert pend.processed_at is None
    assert pend.gateway_response["outcome"] == "pending"


def test_successful_full_payment_via_api_and_audit_logs(api_client, db_session):
    customer, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("99.50"))

    resp = api_client.post(
        "/api/v1/payments",
        json={
            "invoice_id": invoice.id,
            "customer_id": customer.id,
            "amount": "99.50",
            "currency": "usd",
            "payment_method": "credit_card",
            "idempotency_key": "IDEM-FULL-001",
            "simulate_outcome": "succeed",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["invoice_id"] == invoice.id
    assert data["customer_id"] == customer.id
    assert data["amount"] == "99.50"
    assert data["refunded_amount"] == "0.00"
    assert data["remaining_refundable_amount"] == "99.50"
    assert data["currency"] == "USD"
    assert data["payment_method"] == "credit_card"
    assert data["status"] == "succeeded"
    assert data["payment_date"] is not None
    assert data["transaction_reference"].startswith("TXN-")
    assert data["idempotency_key"] == "IDEM-FULL-001"
    assert data["gateway_response"]["simulated"] is True

    db_session.expire_all()
    refreshed_inv = db_session.get(Invoice, invoice.id)
    assert refreshed_inv.amount_paid == Decimal("99.50")
    assert refreshed_inv.status == InvoiceStatus.PAID
    assert refreshed_inv.paid_at is not None

    payment_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == data["id"],
            AuditLog.action == "payment.succeeded",
        )
        .first()
    )
    assert payment_audit is not None
    assert payment_audit.customer_id == customer.id
    assert payment_audit.new_values["status"] == "succeeded"

    invoice_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "invoice",
            AuditLog.entity_id == invoice.id,
            AuditLog.action == "invoice.paid",
        )
        .first()
    )
    assert invoice_audit is not None
    assert invoice_audit.old_values["status"] == "open"
    assert invoice_audit.old_values["amount_paid"] == "0.00"
    assert invoice_audit.new_values["status"] == "paid"
    assert invoice_audit.new_values["amount_paid"] == "99.50"


def test_failed_payment_persisted_without_settling_invoice_and_retry_succeeds(api_client, db_session):
    customer, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("75.00"))

    fail_resp = api_client.post(
        "/api/v1/payments",
        json={
            "invoice_id": invoice.id,
            "simulate_outcome": "fail",
            "failure_reason": "Card declined by mock issuer",
            "payment_method": "credit_card",
        },
    )
    assert fail_resp.status_code == 201
    fail_data = fail_resp.json()
    assert fail_data["status"] == "failed"
    assert fail_data["payment_date"] is None
    assert fail_data["failure_reason"] == "Card declined by mock issuer"
    assert fail_data["remaining_refundable_amount"] == "0.00"

    db_session.expire_all()
    inv_after_fail = db_session.get(Invoice, invoice.id)
    assert inv_after_fail.amount_paid == Decimal("0.00")
    assert inv_after_fail.status == InvoiceStatus.OPEN
    assert inv_after_fail.paid_at is None

    fail_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == fail_data["id"],
            AuditLog.action == "payment.failed",
        )
        .first()
    )
    assert fail_audit is not None

    # Subsequent retry on same OPEN invoice succeeds cleanly
    ok_resp = api_client.post(
        "/api/v1/payments",
        json={
            "invoice_id": invoice.id,
            "simulate_outcome": "succeed",
            "payment_method": "credit_card",
        },
    )
    assert ok_resp.status_code == 201
    assert ok_resp.json()["status"] == "succeeded"

    inv_payments = api_client.get(f"/api/v1/invoices/{invoice.id}/payments")
    assert inv_payments.status_code == 200
    body = inv_payments.json()
    assert body["total"] == 2
    assert {item["status"] for item in body["items"]} == {"failed", "succeeded"}


def test_pending_payment_does_not_credit_invoice_and_blocks_overcommitment(api_client, db_session):
    _, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("100.00"))

    pend_resp = api_client.post(
        "/api/v1/payments",
        json={
            "invoice_id": invoice.id,
            "amount": "60.00",
            "simulate_outcome": "pending",
            "payment_method": "bank_transfer",
        },
    )
    assert pend_resp.status_code == 201
    pend_data = pend_resp.json()
    assert pend_data["status"] == "pending"
    assert pend_data["payment_date"] is None
    assert pend_data["remaining_refundable_amount"] == "0.00"

    db_session.expire_all()
    inv_after_pend = db_session.get(Invoice, invoice.id)
    assert inv_after_pend.amount_paid == Decimal("0.00")
    assert inv_after_pend.status == InvoiceStatus.OPEN

    pend_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == pend_data["id"],
            AuditLog.action == "payment.initiated",
        )
        .first()
    )
    assert pend_audit is not None

    # Attempting to charge more than remaining uncommitted 40.00 is rejected
    over_commit = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": invoice.id, "amount": "50.00"},
    )
    assert over_commit.status_code == 400
    assert "pending payment" in over_commit.json()["detail"].lower()

    # Remaining uncommitted 40.00 can still be paid cleanly
    rem_resp = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": invoice.id, "amount": "40.00"},
    )
    assert rem_resp.status_code == 201
    assert rem_resp.json()["status"] == "succeeded"

    db_session.expire_all()
    inv_after_rem = db_session.get(Invoice, invoice.id)
    assert inv_after_rem.amount_paid == Decimal("40.00")
    assert inv_after_rem.status == InvoiceStatus.OPEN


def test_partial_invoice_payments_accumulate_to_paid_status_and_api_queries(api_client, db_session):
    c1, inv1 = _seed_customer_and_invoice(db_session, total_amount=Decimal("100.00"))
    c2, inv2 = _seed_customer_and_invoice(db_session, total_amount=Decimal("50.00"))

    p1 = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv1.id, "amount": "40.00", "payment_method": "upi"},
    )
    assert p1.status_code == 201

    db_session.expire_all()
    inv1_mid = db_session.get(Invoice, inv1.id)
    assert inv1_mid.amount_paid == Decimal("40.00")
    assert inv1_mid.status == InvoiceStatus.OPEN
    assert inv1_mid.paid_at is None

    partial_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "invoice",
            AuditLog.entity_id == inv1.id,
            AuditLog.action == "invoice.partially_paid",
        )
        .first()
    )
    assert partial_audit is not None

    p2 = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv1.id, "amount": "60.00", "payment_method": "credit_card"},
    )
    assert p2.status_code == 201

    db_session.expire_all()
    inv1_done = db_session.get(Invoice, inv1.id)
    assert inv1_done.amount_paid == Decimal("100.00")
    assert inv1_done.status == InvoiceStatus.PAID
    assert inv1_done.paid_at is not None

    p3 = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv2.id, "amount": "50.00", "payment_method": "debit_card"},
    )
    assert p3.status_code == 201

    # Pagination & filtering on GET /api/v1/payments
    list_page1 = api_client.get("/api/v1/payments?page=1&size=2")
    assert list_page1.status_code == 200
    body1 = list_page1.json()
    assert body1["total"] == 3
    assert body1["pages"] == 2
    assert len(body1["items"]) == 2

    by_customer = api_client.get(f"/api/v1/payments?customer_id={c2.id}")
    assert by_customer.status_code == 200
    assert by_customer.json()["total"] == 1
    assert by_customer.json()["items"][0]["invoice_id"] == inv2.id

    by_method = api_client.get("/api/v1/payments?payment_method=upi")
    assert by_method.status_code == 200
    assert by_method.json()["total"] == 1
    assert by_method.json()["items"][0]["id"] == p1.json()["id"]

    # GET /api/v1/payments/{payment_id} & 404
    detail = api_client.get(f"/api/v1/payments/{p1.json()['id']}")
    assert detail.status_code == 200
    assert detail.json()["amount"] == "40.00"
    assert api_client.get("/api/v1/payments/999999").status_code == 404

    # GET /api/v1/invoices/{invoice_id}/payments & 404
    inv1_payments = api_client.get(f"/api/v1/invoices/{inv1.id}/payments?status=succeeded")
    assert inv1_payments.status_code == 200
    assert inv1_payments.json()["total"] == 2
    assert api_client.get("/api/v1/invoices/999999/payments").status_code == 404


def test_overpayment_invalid_status_ownership_and_currency_rejections(api_client, db_session):
    c1, inv_open = _seed_customer_and_invoice(db_session, total_amount=Decimal("100.00"), currency="USD")
    c2, inv_draft = _seed_customer_and_invoice(
        db_session, total_amount=Decimal("50.00"), status=InvoiceStatus.DRAFT
    )
    _, inv_void = _seed_customer_and_invoice(
        db_session, total_amount=Decimal("50.00"), status=InvoiceStatus.VOID
    )
    _, inv_uncollectible = _seed_customer_and_invoice(
        db_session, total_amount=Decimal("50.00"), status=InvoiceStatus.UNCOLLECTIBLE
    )
    _, inv_paid = _seed_customer_and_invoice(
        db_session, total_amount=Decimal("50.00"), amount_paid=Decimal("50.00"), status=InvoiceStatus.PAID
    )
    _, inv_zero = _seed_customer_and_invoice(
        db_session, total_amount=Decimal("0.00"), status=InvoiceStatus.OPEN
    )

    # Non-existent invoice -> 404
    assert api_client.post("/api/v1/payments", json={"invoice_id": 999999}).status_code == 404

    # Overpayment -> 400
    over_resp = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv_open.id, "amount": "100.01"},
    )
    assert over_resp.status_code == 400
    assert "exceeds remaining" in over_resp.json()["detail"].lower()

    # Ownership mismatch -> 400
    owner_resp = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv_open.id, "customer_id": c2.id, "amount": "20.00"},
    )
    assert owner_resp.status_code == 400
    assert "does not match invoice owner" in owner_resp.json()["detail"].lower()

    # Currency mismatch -> 400
    curr_resp = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv_open.id, "customer_id": c1.id, "amount": "20.00", "currency": "EUR"},
    )
    assert curr_resp.status_code == 400
    assert "currency" in curr_resp.json()["detail"].lower()

    # Invalid invoice states & $0.00 invoice -> 400
    for invalid_inv in (inv_draft, inv_void, inv_uncollectible, inv_paid, inv_zero):
        bad = api_client.post("/api/v1/payments", json={"invoice_id": invalid_inv.id})
        assert bad.status_code == 400

    # Partial payment followed by overpayment of remaining balance -> 400
    assert api_client.post(
        "/api/v1/payments", json={"invoice_id": inv_open.id, "amount": "40.00"}
    ).status_code == 201
    over_partial = api_client.post(
        "/api/v1/payments", json={"invoice_id": inv_open.id, "amount": "60.01"}
    )
    assert over_partial.status_code == 400
    assert db_session.query(Payment).filter(Payment.invoice_id == inv_open.id).count() == 1


def test_duplicate_idempotency_key_and_transaction_reference_conflicts(api_client, db_session):
    _, inv1 = _seed_customer_and_invoice(db_session, total_amount=Decimal("80.00"))
    _, inv2 = _seed_customer_and_invoice(db_session, total_amount=Decimal("80.00"))

    # First call succeeds and marks invoice PAID
    first = api_client.post(
        "/api/v1/payments",
        json={
            "invoice_id": inv1.id,
            "amount": "80.00",
            "idempotency_key": "RETRY-KEY-1",
            "transaction_reference": "TXN-CUSTOM-001",
        },
    )
    assert first.status_code == 201
    first_data = first.json()

    # Exact replay (even though inv1 is now PAID) returns HTTP 200 with original record
    replay = api_client.post(
        "/api/v1/payments",
        json={
            "invoice_id": inv1.id,
            "amount": "80.00",
            "idempotency_key": "RETRY-KEY-1",
            "transaction_reference": "TXN-CUSTOM-001",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first_data["id"]
    assert replay.json()["transaction_reference"] == "TXN-CUSTOM-001"
    assert db_session.query(Payment).filter(Payment.invoice_id == inv1.id).count() == 1

    # Reusing the same idempotency_key with different invoice or amount -> 409 Conflict
    conflict_inv = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv2.id, "amount": "80.00", "idempotency_key": "RETRY-KEY-1"},
    )
    assert conflict_inv.status_code == 409

    conflict_amt = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv1.id, "amount": "40.00", "idempotency_key": "RETRY-KEY-1"},
    )
    assert conflict_amt.status_code == 409

    # Reusing custom transaction_reference on a different payment -> 409 Conflict
    dup_ref = api_client.post(
        "/api/v1/payments",
        json={"invoice_id": inv2.id, "amount": "80.00", "transaction_reference": "TXN-CUSTOM-001"},
    )
    assert dup_ref.status_code == 409


def test_concurrent_payment_attempts_prevent_double_charge(session_factory):
    # Scenario A: 4 concurrent requests without idempotency key trying to charge full $100.00
    db1 = session_factory()
    try:
        _, inv_a = _seed_customer_and_invoice(db1, total_amount=Decimal("100.00"))
        inv_a_id = inv_a.id
    finally:
        db1.close()

    def _charge_no_key():
        wdb = session_factory()
        try:
            payment, created = PaymentService.process_payment(
                wdb,
                PaymentInitiateRequest(invoice_id=inv_a_id, amount=Decimal("100.00")),
                actor="concurrent_worker",
            )
            return ("ok", payment.id, created)
        except InvalidPaymentOperationError as exc:
            wdb.rollback()
            return ("rejected", str(exc), False)
        finally:
            wdb.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes_a = list(pool.map(lambda _: _charge_no_key(), range(4)))

    ok_outcomes = [o for o in outcomes_a if o[0] == "ok"]
    rejected_outcomes = [o for o in outcomes_a if o[0] == "rejected"]
    assert len(ok_outcomes) == 1
    assert len(rejected_outcomes) == 3

    db_verify_a = session_factory()
    try:
        inv_check = db_verify_a.get(Invoice, inv_a_id)
        assert inv_check.amount_paid == Decimal("100.00")
        assert inv_check.status == InvoiceStatus.PAID
        assert db_verify_a.query(Payment).filter(Payment.invoice_id == inv_a_id).count() == 1
    finally:
        db_verify_a.close()

    # Scenario B: 4 concurrent retries sharing the SAME idempotency_key -> 1 created, 3 replayed
    db2 = session_factory()
    try:
        _, inv_b = _seed_customer_and_invoice(db2, total_amount=Decimal("100.00"))
        inv_b_id = inv_b.id
    finally:
        db2.close()

    shared_key = f"CONC-KEY-{_uid()}"

    def _charge_same_key():
        wdb = session_factory()
        try:
            payment, created = PaymentService.process_payment(
                wdb,
                PaymentInitiateRequest(
                    invoice_id=inv_b_id,
                    amount=Decimal("100.00"),
                    idempotency_key=shared_key,
                ),
                actor="concurrent_retry_worker",
            )
            return payment.id, payment.transaction_reference, created
        finally:
            wdb.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes_b = list(pool.map(lambda _: _charge_same_key(), range(4)))

    created_flags = [flag for _, _, flag in outcomes_b]
    payment_ids = {pid for pid, _, _ in outcomes_b}
    refs = {ref for _, ref, _ in outcomes_b}
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 3
    assert len(payment_ids) == 1
    assert len(refs) == 1

    db_verify_b = session_factory()
    try:
        inv_b_check = db_verify_b.get(Invoice, inv_b_id)
        assert inv_b_check.amount_paid == Decimal("100.00")
        assert inv_b_check.status == InvoiceStatus.PAID
        assert db_verify_b.query(Payment).filter(Payment.invoice_id == inv_b_id).count() == 1
    finally:
        db_verify_b.close()


def test_payment_transaction_rollback_on_failure(session_factory):
    db_seed = session_factory()
    try:
        _, invoice = _seed_customer_and_invoice(db_seed, total_amount=Decimal("65.00"))
        invoice_id = invoice.id
    finally:
        db_seed.close()

    db_fail = session_factory()
    try:
        with patch.object(db_fail, "commit", side_effect=RuntimeError("Simulated payment DB crash")):
            with pytest.raises(RuntimeError, match="Simulated payment DB crash"):
                PaymentService.process_payment(
                    db_fail,
                    PaymentInitiateRequest(invoice_id=invoice_id, amount=Decimal("65.00")),
                )
        db_fail.rollback()
    finally:
        db_fail.close()

    db_verify = session_factory()
    try:
        refreshed = db_verify.get(Invoice, invoice_id)
        assert refreshed.amount_paid == Decimal("0.00")
        assert refreshed.status == InvoiceStatus.OPEN
        assert refreshed.paid_at is None
        assert db_verify.query(Payment).filter(Payment.invoice_id == invoice_id).count() == 0
        assert (
            db_verify.query(AuditLog)
            .filter(AuditLog.entity_type.in_(["payment", "invoice"]))
            .count()
            == 0
        )
    finally:
        db_verify.close()


def test_mock_payment_gateway_80_20_probability_distribution():
    # Injectable float testing
    assert MockPaymentGateway.determine_automatic_outcome(0.00) == MockPaymentOutcome.SUCCEED
    assert MockPaymentGateway.determine_automatic_outcome(0.50) == MockPaymentOutcome.SUCCEED
    assert MockPaymentGateway.determine_automatic_outcome(0.7999) == MockPaymentOutcome.SUCCEED
    assert MockPaymentGateway.determine_automatic_outcome(0.80) == MockPaymentOutcome.FAIL
    assert MockPaymentGateway.determine_automatic_outcome(0.99) == MockPaymentOutcome.FAIL

    # Mocking random.random
    with patch("random.random", return_value=0.15):
        assert MockPaymentGateway.determine_automatic_outcome() == MockPaymentOutcome.SUCCEED
    with patch("random.random", return_value=0.85):
        assert MockPaymentGateway.determine_automatic_outcome() == MockPaymentOutcome.FAIL


def test_process_payment_endpoint_automatic_success_and_invoice_paid(api_client, db_session):
    _, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("120.00"))

    # Test via top-level endpoint POST /payments/process with mocked random < 0.80
    with patch("random.random", return_value=0.25):
        res = api_client.post(
            "/payments/process",
            json={"invoice_id": invoice.id, "amount": "120.00"},
        )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["transaction_reference"].startswith("TXN-")
    assert body["amount"] == "120.00"
    assert body["payment_date"] is not None
    assert body["gateway_response"]["simulated"] is True
    assert body["gateway_response"]["outcome"] == "succeeded"

    db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_paid == Decimal("120.00")
    assert invoice.paid_at is not None


def test_process_payment_endpoint_automatic_failure_leaves_invoice_open(api_client, db_session):
    _, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("200.00"))

    # Test via /api/v1/payments/process with mocked random >= 0.80
    with patch("random.random", return_value=0.90):
        res = api_client.post(
            "/api/v1/payments/process",
            json={"invoice_id": invoice.id, "amount": "200.00"},
        )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "failed"
    assert body["transaction_reference"].startswith("TXN-")
    assert body["failure_reason"] is not None
    assert body["payment_date"] is None
    assert body["gateway_response"]["outcome"] == "failed"

    db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.OPEN
    assert invoice.amount_paid == Decimal("0.00")
    assert invoice.paid_at is None


def test_process_payment_endpoint_injectable_random_value(api_client, db_session):
    _, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("150.00"))

    # Inject random_value=0.05 -> should succeed
    res_ok = api_client.post(
        "/payments/process",
        json={"invoice_id": invoice.id, "amount": "50.00", "random_value": 0.05},
    )
    assert res_ok.status_code == 201
    assert res_ok.json()["status"] == "succeeded"

    db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.OPEN
    assert invoice.amount_paid == Decimal("50.00")

    # Inject random_value=0.95 -> should fail
    res_fail = api_client.post(
        "/payments/process",
        json={"invoice_id": invoice.id, "amount": "50.00", "random_value": 0.95},
    )
    assert res_fail.status_code == 201
    assert res_fail.json()["status"] == "failed"

    db_session.refresh(invoice)
    assert invoice.amount_paid == Decimal("50.00")


def test_process_payment_endpoint_default_amount_to_remaining_balance(api_client, db_session):
    _, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("75.50"))

    # Omit amount completely, with injected success
    res = api_client.post(
        "/payments/process",
        json={"invoice_id": invoice.id, "random_value": 0.10},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "succeeded"
    assert body["amount"] == "75.50"

    db_session.refresh(invoice)
    assert invoice.status == InvoiceStatus.PAID
    assert invoice.amount_paid == Decimal("75.50")


def test_process_payment_endpoint_invalid_invoice_and_amount_rejections(api_client, db_session):
    _, invoice = _seed_customer_and_invoice(db_session, total_amount=Decimal("100.00"))

    # 1. Non-existent invoice
    res_404 = api_client.post("/payments/process", json={"invoice_id": 999999, "amount": "50.00"})
    assert res_404.status_code == 404

    # 2. Overpayment (amount > balance)
    res_over = api_client.post("/payments/process", json={"invoice_id": invoice.id, "amount": "150.00"})
    assert res_over.status_code == 400
    assert "exceeds remaining invoice balance" in res_over.json()["detail"]

    # 3. Zero amount
    res_zero = api_client.post("/payments/process", json={"invoice_id": invoice.id, "amount": "0.00"})
    assert res_zero.status_code in (400, 422)

    # 4. Settle invoice, then try paying already-paid invoice
    with patch("random.random", return_value=0.1):
        res_pay = api_client.post("/payments/process", json={"invoice_id": invoice.id, "amount": "100.00"})
        assert res_pay.status_code == 201

    res_already_paid = api_client.post("/payments/process", json={"invoice_id": invoice.id, "amount": "10.00"})
    assert res_already_paid.status_code == 400
    assert "cannot accept payments in 'paid' status" in res_already_paid.json()["detail"]


