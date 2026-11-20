"""
Stage 4 Integration, Accounting, Security & Concurrency Tests for Refund Processing:
- Partial, multiple-partial, and full refunds via MockPaymentGateway & /payments/{id}/refund
- Pending & failed refund handling and uncommitted refund balance protection
- Refund idempotency replay & parameter-conflict detection
- Concurrent refund attempts via ThreadPoolExecutor (Invoice -> Payment row locking)
- Signed refund.succeeded & refund.failed webhooks, deduplication & stale protection
- Historical Invoice.amount_paid & InvoiceStatus.PAID preservation + net collected calculation
- Immutable AuditLog records & transaction rollback safety
Executes strictly against subscription_billing_test_db.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Optional
import uuid
from unittest.mock import patch
import pytest
from app.core.config import settings
from app.models import (
    AuditLog,
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
from app.schemas.payment import MockWebhookPayload, PaymentRefundRequest
from app.services.mock_payment_gateway import MockPaymentGateway, MockPaymentOutcome
from app.services.payment_service import (
    DuplicatePaymentError,
    InvalidPaymentOperationError,
    PaymentService,
    WebhookService,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def _seed_settled_payment(
    db,
    *,
    invoice_total: Decimal = Decimal("100.00"),
    payment_amount: Decimal = Decimal("100.00"),
    payment_status: PaymentStatus = PaymentStatus.SUCCEEDED,
    invoice_status: InvoiceStatus = InvoiceStatus.PAID,
    invoice_paid: Optional[Decimal] = None,
):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"REFCUST-{tag}",
        full_name=f"Refund User {tag}",
        email=f"ref.user.{tag.lower()}@example.com",
        currency="USD",
    )
    db.add(customer)
    db.flush()

    effective_paid = (
        invoice_paid
        if invoice_paid is not None
        else (invoice_total if invoice_status == InvoiceStatus.PAID else Decimal("0.00"))
    )
    invoice = Invoice(
        invoice_number=f"INV-REF-{tag}",
        customer_id=customer.id,
        subtotal=invoice_total,
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=invoice_total,
        amount_paid=effective_paid,
        currency="USD",
        status=invoice_status,
        issue_date=now,
        due_date=now + timedelta(days=7),
        paid_at=now if invoice_status == InvoiceStatus.PAID else None,
    )
    db.add(invoice)
    db.flush()

    payment = Payment(
        transaction_reference=f"TXN-REFSRC-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=payment_amount,
        refunded_amount=payment_amount if payment_status == PaymentStatus.REFUNDED else Decimal("0.00"),
        currency="USD",
        payment_method=PaymentMethod.CREDIT_CARD,
        status=payment_status,
        payment_date=now if payment_status in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED) else None,
    )
    db.add(payment)
    db.commit()
    db.refresh(customer)
    db.refresh(invoice)
    db.refresh(payment)
    return customer, invoice, payment


def _signed_headers(raw_body: bytes, timestamp: int = None, secret: str = None):
    return {
        "Content-Type": "application/json",
        "X-Mock-Signature": MockPaymentGateway.sign_webhook_payload(
            raw_body, secret=secret, timestamp=timestamp
        ),
    }


def test_partial_and_multiple_partial_refunds_preserve_invoice_accounting(api_client, db_session):
    customer, invoice, payment = _seed_settled_payment(
        db_session, invoice_total=Decimal("100.00"), payment_amount=Decimal("100.00")
    )
    orig_txn_ref = payment.transaction_reference

    # 1. First partial refund: 30.00 -> Payment stays SUCCEEDED
    r1 = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "30.00", "reason": "First partial adjustment"},
    )
    assert r1.status_code == 201
    d1 = r1.json()
    assert d1["status"] == "succeeded"
    assert d1["amount"] == "30.00"
    assert d1["payment_status"] == "succeeded"
    assert d1["payment_refunded_amount"] == "30.00"
    assert d1["remaining_refundable_amount"] == "70.00"
    assert d1["invoice_status"] == "paid"
    assert d1["invoice_amount_paid"] == "100.00"
    assert d1["invoice_net_collected_amount"] == "70.00"

    # 2. Second partial refund: 45.00 -> Payment stays SUCCEEDED
    r2 = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "45.00", "reason": "Second partial adjustment"},
    )
    assert r2.status_code == 201
    d2 = r2.json()
    assert d2["payment_status"] == "succeeded"
    assert d2["payment_refunded_amount"] == "75.00"
    assert d2["remaining_refundable_amount"] == "25.00"
    assert d2["invoice_amount_paid"] == "100.00"
    assert d2["invoice_net_collected_amount"] == "25.00"

    # Verify payment detail response exposes refund history & balances
    detail = api_client.get(f"/api/v1/payments/{payment.id}")
    assert detail.status_code == 200
    detail_json = detail.json()
    assert detail_json["amount"] == "100.00"
    assert detail_json["transaction_reference"] == orig_txn_ref
    assert detail_json["refunded_amount"] == "75.00"
    assert detail_json["remaining_refundable_amount"] == "25.00"
    assert len(detail_json["refunds"]) == 2
    assert [ref["amount"] for ref in detail_json["refunds"]] == ["30.00", "45.00"]

    # 3. Third partial refund settling remaining 25.00 -> Payment transitions to REFUNDED
    r3 = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"reason": "Final remaining refund"},
    )
    assert r3.status_code == 201
    d3 = r3.json()
    assert d3["amount"] == "25.00"
    assert d3["payment_status"] == "refunded"
    assert d3["payment_refunded_amount"] == "100.00"
    assert d3["remaining_refundable_amount"] == "0.00"
    assert d3["invoice_status"] == "paid"
    assert d3["invoice_amount_paid"] == "100.00"
    assert d3["invoice_net_collected_amount"] == "0.00"

    # Verify original payment & invoice history preserved and accidental re-billing is blocked
    db_session.expire_all()
    p_db = db_session.get(Payment, payment.id)
    i_db = db_session.get(Invoice, invoice.id)
    assert p_db.amount == Decimal("100.00")
    assert p_db.transaction_reference == orig_txn_ref
    assert i_db.amount_paid == Decimal("100.00")
    assert i_db.status == InvoiceStatus.PAID
    assert i_db.net_collected_amount == Decimal("0.00")

    recharge_attempt = api_client.post("/api/v1/payments", json={"invoice_id": invoice.id})
    assert recharge_attempt.status_code == 400

    # Verify immutable audit events for partial and full completion
    partial_logs = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == payment.id,
            AuditLog.action == "payment.refunded_partial",
        )
        .count()
    )
    full_logs = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == payment.id,
            AuditLog.action == "payment.refunded_full",
        )
        .count()
    )
    assert partial_logs == 2
    assert full_logs == 1
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "payment_refund", AuditLog.action == "payment_refund.succeeded")
        .count()
        == 3
    )


def test_full_refund_in_one_call_and_validation_rejections(api_client, db_session):
    _, invoice, payment_ok = _seed_settled_payment(db_session, invoice_total=Decimal("80.00"), payment_amount=Decimal("80.00"))
    _, _, payment_pend = _seed_settled_payment(
        db_session,
        invoice_total=Decimal("80.00"),
        payment_amount=Decimal("80.00"),
        payment_status=PaymentStatus.PENDING,
        invoice_status=InvoiceStatus.OPEN,
        invoice_paid=Decimal("0.00"),
    )
    _, _, payment_fail = _seed_settled_payment(
        db_session,
        invoice_total=Decimal("80.00"),
        payment_amount=Decimal("80.00"),
        payment_status=PaymentStatus.FAILED,
        invoice_status=InvoiceStatus.OPEN,
        invoice_paid=Decimal("0.00"),
    )

    # Unknown payment -> 404
    assert api_client.post("/api/v1/payments/999999/refund", json={}).status_code == 404

    # Pending and Failed payments cannot be refunded -> 400
    assert api_client.post(f"/api/v1/payments/{payment_pend.id}/refund", json={}).status_code == 400
    assert api_client.post(f"/api/v1/payments/{payment_fail.id}/refund", json={}).status_code == 400

    # Zero & negative amounts rejected -> 422 via Pydantic, InvalidPaymentOperationError in service
    assert api_client.post(f"/api/v1/payments/{payment_ok.id}/refund", json={"amount": "0.00"}).status_code == 422
    assert api_client.post(f"/api/v1/payments/{payment_ok.id}/refund", json={"amount": "-5.00"}).status_code == 422
    with pytest.raises(InvalidPaymentOperationError):
        PaymentService.refund_payment(
            db_session,
            payment_ok.id,
            PaymentRefundRequest.model_construct(amount=Decimal("0.00")),
        )
    db_session.rollback()

    # Currency mismatch rejected -> 400
    assert (
        api_client.post(
            f"/api/v1/payments/{payment_ok.id}/refund",
            json={"amount": "10.00", "currency": "EUR"},
        ).status_code
        == 400
    )

    # Exceeding remaining refundable balance rejected -> 400
    over = api_client.post(f"/api/v1/payments/{payment_ok.id}/refund", json={"amount": "80.01"})
    assert over.status_code == 400
    assert "exceeds remaining refundable" in over.json()["detail"].lower()

    # Full refund with default payload {} succeeds cleanly
    full = api_client.post(f"/api/v1/payments/{payment_ok.id}/refund")
    assert full.status_code == 201
    full_json = full.json()
    assert full_json["amount"] == "80.00"
    assert full_json["payment_status"] == "refunded"
    assert full_json["invoice_status"] == "paid"
    assert full_json["invoice_amount_paid"] == "80.00"
    assert full_json["invoice_net_collected_amount"] == "0.00"

    # Further refund on already REFUNDED payment rejected -> 400
    again = api_client.post(f"/api/v1/payments/{payment_ok.id}/refund", json={"amount": "1.00"})
    assert again.status_code == 400


def test_failed_and_pending_refund_outcomes_and_uncommitted_balance_guard(api_client, db_session):
    _, invoice, payment = _seed_settled_payment(db_session, invoice_total=Decimal("100.00"), payment_amount=Decimal("100.00"))

    # 1. Failed refund does not alter refunded_amount or payment status
    r_fail = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={
            "amount": "50.00",
            "simulate_outcome": "fail",
            "failure_reason": "Gateway bank timeout",
        },
    )
    assert r_fail.status_code == 201
    d_fail = r_fail.json()
    assert d_fail["status"] == "failed"
    assert d_fail["payment_status"] == "succeeded"
    assert d_fail["payment_refunded_amount"] == "0.00"
    assert d_fail["remaining_refundable_amount"] == "100.00"
    assert d_fail["invoice_net_collected_amount"] == "100.00"
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == d_fail["id"], AuditLog.action == "payment_refund.failed")
        .count()
        == 1
    )

    # 2. Pending refund does not increase refunded_amount yet, but reserves capacity
    r_pend = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "60.00", "simulate_outcome": "pending", "reason": "Bank settlement pending"},
    )
    assert r_pend.status_code == 201
    d_pend = r_pend.json()
    assert d_pend["status"] == "pending"
    assert d_pend["payment_status"] == "succeeded"
    assert d_pend["payment_refunded_amount"] == "0.00"
    assert d_pend["remaining_refundable_amount"] == "100.00"
    assert d_pend["invoice_net_collected_amount"] == "100.00"
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == d_pend["id"], AuditLog.action == "payment_refund.initiated")
        .count()
        == 1
    )

    # Over-committing above uncommitted 40.00 fails cleanly
    over_uncommit = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "45.00"},
    )
    assert over_uncommit.status_code == 400
    assert "pending refund" in over_uncommit.json()["detail"].lower()

    # Remaining 40.00 succeeds without touching invoice.amount_paid
    r_rem = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "40.00"},
    )
    assert r_rem.status_code == 201
    assert r_rem.json()["payment_refunded_amount"] == "40.00"
    assert r_rem.json()["payment_status"] == "succeeded"
    assert r_rem.json()["invoice_amount_paid"] == "100.00"
    assert r_rem.json()["invoice_net_collected_amount"] == "60.00"


def test_refund_idempotency_key_replay_and_conflict(api_client, db_session):
    _, _, pay1 = _seed_settled_payment(db_session, invoice_total=Decimal("90.00"), payment_amount=Decimal("90.00"))
    _, _, pay2 = _seed_settled_payment(db_session, invoice_total=Decimal("90.00"), payment_amount=Decimal("90.00"))

    # Full refund with idempotency_key -> payment becomes REFUNDED
    r1 = api_client.post(
        f"/api/v1/payments/{pay1.id}/refund",
        json={
            "amount": "90.00",
            "reason": "Customer requested cancellation",
            "idempotency_key": "REF-KEY-001",
            "refund_reference": "REF-CUSTOM-001",
        },
    )
    assert r1.status_code == 201
    r1_data = r1.json()
    assert r1_data["payment_status"] == "refunded"

    # Replay after payment is REFUNDED still returns 200 OK with original refund & logs duplicate_request
    r2 = api_client.post(
        f"/api/v1/payments/{pay1.id}/refund",
        json={
            "amount": "90.00",
            "reason": "Customer requested cancellation",
            "idempotency_key": "REF-KEY-001",
            "refund_reference": "REF-CUSTOM-001",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r1_data["id"]
    assert r2.json()["refund_reference"] == "REF-CUSTOM-001"
    assert db_session.query(PaymentRefund).filter(PaymentRefund.payment_id == pay1.id).count() == 1
    assert (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment_refund",
            AuditLog.entity_id == r1_data["id"],
            AuditLog.action == "payment_refund.duplicate_request",
        )
        .count()
        == 1
    )

    # Reusing key with different amount, reason, or payment_id -> 409 Conflict
    assert (
        api_client.post(
            f"/api/v1/payments/{pay1.id}/refund",
            json={"amount": "45.00", "idempotency_key": "REF-KEY-001"},
        ).status_code
        == 409
    )
    assert (
        api_client.post(
            f"/api/v1/payments/{pay1.id}/refund",
            json={"amount": "90.00", "reason": "Different reason", "idempotency_key": "REF-KEY-001"},
        ).status_code
        == 409
    )
    assert (
        api_client.post(
            f"/api/v1/payments/{pay2.id}/refund",
            json={"amount": "90.00", "idempotency_key": "REF-KEY-001"},
        ).status_code
        == 409
    )

    # Duplicate custom refund_reference on pay2 -> 409 Conflict
    assert (
        api_client.post(
            f"/api/v1/payments/{pay2.id}/refund",
            json={"amount": "20.00", "refund_reference": "REF-CUSTOM-001"},
        ).status_code
        == 409
    )


def test_concurrent_refund_attempts_prevent_over_refunding(session_factory):
    # Scenario A: 4 concurrent 60.00 refunds on a 100.00 payment without shared key -> only 1 succeeds
    db_a = session_factory()
    try:
        _, inv_a, pay_a = _seed_settled_payment(db_a, invoice_total=Decimal("100.00"), payment_amount=Decimal("100.00"))
        pay_a_id = pay_a.id
        inv_a_id = inv_a.id
    finally:
        db_a.close()

    def _refund_no_key():
        wdb = session_factory()
        try:
            refund, created = PaymentService.refund_payment(
                wdb,
                pay_a_id,
                PaymentRefundRequest(amount=Decimal("60.00"), reason="Concurrent partial"),
                actor="concurrent_refund_worker",
            )
            return ("ok", refund.id, created)
        except InvalidPaymentOperationError as exc:
            wdb.rollback()
            return ("rejected", str(exc), False)
        finally:
            wdb.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes_a = list(pool.map(lambda _: _refund_no_key(), range(4)))

    assert sum(1 for tag, _, _ in outcomes_a if tag == "ok") == 1
    assert sum(1 for tag, _, _ in outcomes_a if tag == "rejected") == 3

    db_ver_a = session_factory()
    try:
        p_check = db_ver_a.get(Payment, pay_a_id)
        i_check = db_ver_a.get(Invoice, inv_a_id)
        assert p_check.refunded_amount == Decimal("60.00")
        assert p_check.status == PaymentStatus.SUCCEEDED
        assert i_check.amount_paid == Decimal("100.00")
        assert i_check.net_collected_amount == Decimal("40.00")
        assert db_ver_a.query(PaymentRefund).filter(PaymentRefund.payment_id == pay_a_id).count() == 1
    finally:
        db_ver_a.close()

    # Scenario B: 4 concurrent retries sharing the SAME idempotency_key -> 1 created, 3 replayed
    db_b = session_factory()
    try:
        _, _, pay_b = _seed_settled_payment(db_b, invoice_total=Decimal("100.00"), payment_amount=Decimal("100.00"))
        pay_b_id = pay_b.id
    finally:
        db_b.close()

    shared_key = f"REF-CONC-{_uid()}"

    def _refund_same_key():
        wdb = session_factory()
        try:
            refund, created = PaymentService.refund_payment(
                wdb,
                pay_b_id,
                PaymentRefundRequest(amount=Decimal("100.00"), idempotency_key=shared_key),
                actor="concurrent_refund_retry",
            )
            return refund.id, refund.refund_reference, created
        finally:
            wdb.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes_b = list(pool.map(lambda _: _refund_same_key(), range(4)))

    created_flags = [flag for _, _, flag in outcomes_b]
    assert created_flags.count(True) == 1
    assert created_flags.count(False) == 3
    assert len({rid for rid, _, _ in outcomes_b}) == 1

    db_ver_b = session_factory()
    try:
        p_b_check = db_ver_b.get(Payment, pay_b_id)
        assert p_b_check.refunded_amount == Decimal("100.00")
        assert p_b_check.status == PaymentStatus.REFUNDED
        assert db_ver_b.query(PaymentRefund).filter(PaymentRefund.payment_id == pay_b_id).count() == 1
    finally:
        db_ver_b.close()


def test_refund_webhooks_signature_settlement_duplicate_and_stale_handling(api_client, db_session):
    _, invoice, payment = _seed_settled_payment(db_session, invoice_total=Decimal("100.00"), payment_amount=Decimal("100.00"))

    # Create two pending refunds: 40.00 (will succeed via webhook) and 60.00 (will fail via webhook)
    r_part = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "40.00", "simulate_outcome": "pending"},
    ).json()
    r_fail = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "60.00", "simulate_outcome": "pending"},
    ).json()

    # 1. Invalid signature on refund webhook -> 401 and zero mutations
    raw_part = json.dumps(
        {
            "event_id": f"EVT-REFSUCC-{_uid()}",
            "event_type": "refund.succeeded",
            "refund_reference": r_part["refund_reference"],
            "transaction_reference": payment.transaction_reference,
            "amount": "40.00",
            "currency": "USD",
        }
    ).encode("utf-8")
    assert (
        api_client.post(
            "/api/v1/webhooks/mock-payment",
            content=raw_part,
            headers=_signed_headers(raw_part, secret="bad_secret"),
        ).status_code
        == 401
    )

    # 2. Expired timestamp on refund webhook -> 401
    old_ts = int(datetime.now(timezone.utc).timestamp()) - settings.MOCK_PAYMENT_WEBHOOK_TOLERANCE_SECONDS - 20
    assert (
        api_client.post(
            "/api/v1/webhooks/mock-payment",
            content=raw_part,
            headers=_signed_headers(raw_part, timestamp=old_ts),
        ).status_code
        == 401
    )

    # 3. Unknown refund_reference -> 404
    raw_unk = json.dumps(
        {
            "event_id": f"EVT-REFUNK-{_uid()}",
            "event_type": "refund.succeeded",
            "refund_reference": "REF-NOT-FOUND",
        }
    ).encode("utf-8")
    assert (
        api_client.post(
            "/api/v1/webhooks/mock-payment",
            content=raw_unk,
            headers=_signed_headers(raw_unk),
        ).status_code
        == 404
    )

    # 4. Valid refund.succeeded on pending 40.00 refund settles partially
    hdrs_part = _signed_headers(raw_part)
    wh1 = api_client.post("/api/v1/webhooks/mock-payment", content=raw_part, headers=hdrs_part)
    assert wh1.status_code == 200
    w1_json = wh1.json()
    assert w1_json["processing_status"] == "processed"
    assert w1_json["duplicate"] is False
    assert w1_json["refund_id"] == r_part["id"]
    assert w1_json["refund_status"] == "succeeded"
    assert w1_json["payment_status"] == "succeeded"

    # 5. Duplicate refund.succeeded webhook -> idempotent replay
    wh1_dup = api_client.post("/api/v1/webhooks/mock-payment", content=raw_part, headers=hdrs_part)
    assert wh1_dup.status_code == 200
    assert wh1_dup.json()["duplicate"] is True
    assert wh1_dup.json()["refund_id"] == r_part["id"]

    # 6. Stale refund.failed arriving AFTER r_part succeeded must NOT reverse completed refund
    raw_stale_fail = json.dumps(
        {
            "event_id": f"EVT-REFSTALE-FAIL-{_uid()}",
            "event_type": "refund.failed",
            "refund_reference": r_part["refund_reference"],
            "failure_reason": "Late stale failure signal",
        }
    ).encode("utf-8")
    wh_stale = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_stale_fail,
        headers=_signed_headers(raw_stale_fail),
    )
    assert wh_stale.status_code == 200
    assert wh_stale.json()["processing_status"] == "ignored_stale"
    assert wh_stale.json()["refund_status"] == "succeeded"

    # 7. Valid refund.failed on pending r_fail marks r_fail FAILED cleanly
    raw_fail = json.dumps(
        {
            "event_id": f"EVT-REFFAIL-{_uid()}",
            "event_type": "refund.failed",
            "refund_reference": r_fail["refund_reference"],
            "failure_reason": "Card network reversed refund",
        }
    ).encode("utf-8")
    wh_fail = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_fail,
        headers=_signed_headers(raw_fail),
    )
    assert wh_fail.status_code == 200
    assert wh_fail.json()["processing_status"] == "processed"
    assert wh_fail.json()["refund_status"] == "failed"

    # 8. Issue and settle remaining 60.00 via refund.succeeded webhook -> Payment becomes REFUNDED
    r_final = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "60.00", "simulate_outcome": "pending"},
    ).json()
    raw_final = json.dumps(
        {
            "event_id": f"EVT-REFFINAL-{_uid()}",
            "event_type": "refund.succeeded",
            "refund_reference": r_final["refund_reference"],
        }
    ).encode("utf-8")
    wh_final = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_final,
        headers=_signed_headers(raw_final),
    )
    assert wh_final.status_code == 200
    assert wh_final.json()["payment_status"] == "refunded"
    assert wh_final.json()["refund_status"] == "succeeded"

    # Final accounting invariants check
    db_session.expire_all()
    p_end = db_session.get(Payment, payment.id)
    i_end = db_session.get(Invoice, invoice.id)
    assert p_end.status == PaymentStatus.REFUNDED
    assert p_end.refunded_amount == Decimal("100.00")
    assert i_end.status == InvoiceStatus.PAID
    assert i_end.amount_paid == Decimal("100.00")
    assert i_end.total_refunded_amount == Decimal("100.00")
    assert i_end.net_collected_amount == Decimal("0.00")
    assert db_session.get(PaymentRefund, r_part["id"]).status == RefundStatus.SUCCEEDED
    assert db_session.get(PaymentRefund, r_fail["id"]).status == RefundStatus.FAILED
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "webhook.ignored_stale")
        .count()
        == 1
    )


def test_refund_transaction_rollback_on_failure(session_factory):
    db_seed = session_factory()
    try:
        _, invoice, payment = _seed_settled_payment(db_seed, invoice_total=Decimal("80.00"), payment_amount=Decimal("80.00"))
        pay_id = payment.id
        inv_id = invoice.id
    finally:
        db_seed.close()

    db_fail = session_factory()
    try:
        with patch.object(db_fail, "commit", side_effect=RuntimeError("Simulated refund DB crash")):
            with pytest.raises(RuntimeError, match="Simulated refund DB crash"):
                PaymentService.refund_payment(
                    db_fail,
                    pay_id,
                    PaymentRefundRequest(amount=Decimal("40.00")),
                )
        db_fail.rollback()
    finally:
        db_fail.close()

    db_verify = session_factory()
    try:
        p_check = db_verify.get(Payment, pay_id)
        i_check = db_verify.get(Invoice, inv_id)
        assert p_check.refunded_amount == Decimal("0.00")
        assert p_check.status == PaymentStatus.SUCCEEDED
        assert i_check.amount_paid == Decimal("80.00")
        assert i_check.status == InvoiceStatus.PAID
        assert db_verify.query(PaymentRefund).filter(PaymentRefund.payment_id == pay_id).count() == 0
        assert (
            db_verify.query(AuditLog)
            .filter(AuditLog.entity_type.in_(["payment_refund", "payment"]))
            .count()
            == 0
        )
    finally:
        db_verify.close()
