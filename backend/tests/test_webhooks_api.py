"""
Stage 3 Integration, Security & Concurrency Tests for Mock Payment Webhook Processing:
- HMAC-SHA256 X-Mock-Signature verification against exact raw request bytes
- Replay timestamp window validation & constant-time checks
- payment.succeeded (PENDING -> SUCCEEDED) and payment.failed (PENDING -> FAILED)
- Duplicate & concurrent duplicate webhook idempotency (WebhookEvent.event_id)
- Stale failure protection for already SUCCEEDED / REFUNDED payments
- Invalid payment references & invoice balance conflicts
- Immutable audit logs & database transaction rollback safety
Executes strictly against subscription_billing_test_db.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any, Dict, Optional
import uuid
from unittest.mock import patch
import pytest
from app.core.config import settings
from app.models import (
    AuditLog,
    BillingInterval,
    Customer,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentRefund,
    PaymentStatus,
    Plan,
    RefundStatus,
    Subscription,
    SubscriptionStatus,
    WebhookEvent,
)
from app.schemas.payment import MockWebhookPayload
from app.services.mock_payment_gateway import MockPaymentGateway
from app.services.payment_service import WebhookService


def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def _seed_invoice_and_payment(
    db,
    *,
    invoice_total: Decimal = Decimal("100.00"),
    invoice_paid: Decimal = Decimal("0.00"),
    invoice_status: InvoiceStatus = InvoiceStatus.OPEN,
    payment_amount: Decimal = Decimal("100.00"),
    payment_status: PaymentStatus = PaymentStatus.PENDING,
):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"WHCUST-{tag}",
        full_name=f"Webhook User {tag}",
        email=f"wh.user.{tag.lower()}@example.com",
        currency="USD",
    )
    db.add(customer)
    db.flush()

    invoice = Invoice(
        invoice_number=f"INV-WH-{tag}",
        customer_id=customer.id,
        subtotal=invoice_total,
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=invoice_total,
        amount_paid=invoice_paid,
        currency="USD",
        status=invoice_status,
        issue_date=now,
        due_date=now + timedelta(days=7),
        paid_at=now if invoice_status == InvoiceStatus.PAID else None,
    )
    db.add(invoice)
    db.flush()

    payment = Payment(
        transaction_reference=f"TXN-WH-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=payment_amount,
        refunded_amount=payment_amount if payment_status == PaymentStatus.REFUNDED else Decimal("0.00"),
        currency="USD",
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
    sig = MockPaymentGateway.sign_webhook_payload(raw_body, secret=secret, timestamp=timestamp)
    return {
        "Content-Type": "application/json",
        "X-Mock-Signature": sig,
    }


def test_webhook_signature_validation_missing_invalid_tampered_and_replay_window(api_client, db_session):
    _, invoice, payment = _seed_invoice_and_payment(db_session, payment_status=PaymentStatus.PENDING)

    # Non-canonical raw whitespace bytes to prove HMAC runs on exact raw bytes
    raw_body = (
        f'{{\n  "event_id":   "EVT-RAW-{_uid()}",\n'
        f'  "event_type": "payment.succeeded",\n'
        f'  "transaction_reference": "{payment.transaction_reference}"\n}}'
    ).encode("utf-8")

    # 1. Missing signature header -> 401
    r_missing = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert r_missing.status_code == 401

    # 2. Wrong secret / invalid HMAC -> 401
    r_bad_sig = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_body,
        headers=_signed_headers(raw_body, secret="wrong_secret_value"),
    )
    assert r_bad_sig.status_code == 401

    # 3. Malformed signature header -> 401
    r_malformed = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Mock-Signature": "v1=deadbeef"},
    )
    assert r_malformed.status_code == 401

    # 4. Tampered body against previously valid signature -> 401
    valid_headers = _signed_headers(raw_body)
    tampered_body = raw_body.replace(b'"payment.succeeded"', b'"payment.failed"')
    r_tampered = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=tampered_body,
        headers=valid_headers,
    )
    assert r_tampered.status_code == 401

    # 5. Replay timestamp outside tolerance window (too old AND too far in future) -> 401
    now_ts = int(datetime.now(timezone.utc).timestamp())
    expired_ts = now_ts - settings.MOCK_PAYMENT_WEBHOOK_TOLERANCE_SECONDS - 30
    r_expired = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_body,
        headers=_signed_headers(raw_body, timestamp=expired_ts),
    )
    assert r_expired.status_code == 401

    future_ts = now_ts + settings.MOCK_PAYMENT_WEBHOOK_TOLERANCE_SECONDS + 30
    r_future = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_body,
        headers=_signed_headers(raw_body, timestamp=future_ts),
    )
    assert r_future.status_code == 401

    # Confirm zero mutations happened across all 401 rejections
    db_session.expire_all()
    assert db_session.get(Payment, payment.id).status == PaymentStatus.PENDING
    assert db_session.get(Invoice, invoice.id).amount_paid == Decimal("0.00")
    assert db_session.query(WebhookEvent).count() == 0

    # 6. Exact raw bytes with valid signature succeeds -> 200
    r_ok = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_body,
        headers=valid_headers,
    )
    assert r_ok.status_code == 200
    assert r_ok.json()["processing_status"] == "processed"
    assert r_ok.json()["payment_status"] == "succeeded"


def test_webhook_pending_to_succeeded_settles_invoice_and_writes_audit_logs(api_client, db_session):
    customer, invoice, payment = _seed_invoice_and_payment(
        db_session,
        invoice_total=Decimal("120.00"),
        payment_amount=Decimal("120.00"),
        payment_status=PaymentStatus.PENDING,
    )

    event_id = f"EVT-OK-{_uid()}"
    payload_bytes = json.dumps(
        {
            "event_id": event_id,
            "event_type": "payment.succeeded",
            "transaction_reference": payment.transaction_reference,
            "amount": "120.00",
            "currency": "USD",
            "gateway_data": {"processor_trace": "TRC-777"},
        }
    ).encode("utf-8")

    resp = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=payload_bytes,
        headers=_signed_headers(payload_bytes),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event_id
    assert body["processing_status"] == "processed"
    assert body["duplicate"] is False
    assert body["payment_id"] == payment.id
    assert body["payment_status"] == "succeeded"
    assert body["invoice_id"] == invoice.id
    assert body["invoice_status"] == "paid"

    db_session.expire_all()
    p_db = db_session.get(Payment, payment.id)
    i_db = db_session.get(Invoice, invoice.id)
    assert p_db.status == PaymentStatus.SUCCEEDED
    assert p_db.payment_date is not None
    assert p_db.gateway_response["last_webhook_event_id"] == event_id
    assert i_db.amount_paid == Decimal("120.00")
    assert i_db.status == InvoiceStatus.PAID
    assert i_db.paid_at is not None

    pay_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == payment.id,
            AuditLog.action == "payment.succeeded",
        )
        .first()
    )
    assert pay_audit is not None
    assert pay_audit.old_values["status"] == "pending"
    assert pay_audit.new_values["status"] == "succeeded"
    assert pay_audit.customer_id == customer.id

    inv_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "invoice",
            AuditLog.entity_id == invoice.id,
            AuditLog.action == "invoice.paid",
        )
        .first()
    )
    assert inv_audit is not None
    assert inv_audit.old_values["amount_paid"] == "0.00"
    assert inv_audit.new_values["amount_paid"] == "120.00"
    assert inv_audit.new_values["status"] == "paid"

    wh_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "webhook_event",
            AuditLog.action == "webhook.processed",
        )
        .first()
    )
    assert wh_audit is not None


def test_webhook_pending_to_failed_leaves_invoice_open_and_unpaid(api_client, db_session):
    _, invoice, payment = _seed_invoice_and_payment(
        db_session,
        invoice_total=Decimal("85.00"),
        payment_amount=Decimal("85.00"),
        payment_status=PaymentStatus.PENDING,
    )

    event_id = f"EVT-FAIL-{_uid()}"
    payload_bytes = json.dumps(
        {
            "event_id": event_id,
            "event_type": "payment.failed",
            "transaction_reference": payment.transaction_reference,
            "failure_reason": "ACH return R01 insufficient funds",
        }
    ).encode("utf-8")

    resp = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=payload_bytes,
        headers=_signed_headers(payload_bytes),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["processing_status"] == "processed"
    assert body["duplicate"] is False
    assert body["payment_status"] == "failed"
    assert body["invoice_status"] == "open"

    db_session.expire_all()
    p_db = db_session.get(Payment, payment.id)
    i_db = db_session.get(Invoice, invoice.id)
    assert p_db.status == PaymentStatus.FAILED
    assert p_db.failure_reason == "ACH return R01 insufficient funds"
    assert p_db.payment_date is None
    assert i_db.amount_paid == Decimal("0.00")
    assert i_db.status == InvoiceStatus.OPEN
    assert i_db.paid_at is None

    fail_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "payment",
            AuditLog.entity_id == payment.id,
            AuditLog.action == "payment.failed",
        )
        .first()
    )
    assert fail_audit is not None
    assert fail_audit.old_values["status"] == "pending"
    assert fail_audit.new_values["status"] == "failed"


def test_webhook_duplicate_and_concurrent_duplicate_deliveries_idempotent(api_client, session_factory):
    # Sequential HTTP duplicate replay
    db_s = session_factory()
    try:
        _, inv1, pay1 = _seed_invoice_and_payment(db_s, invoice_total=Decimal("90.00"), payment_amount=Decimal("90.00"))
        inv1_id = inv1.id
        txn1 = pay1.transaction_reference
    finally:
        db_s.close()

    evt1 = f"EVT-DUP-{_uid()}"
    raw1 = json.dumps(
        {
            "event_id": evt1,
            "event_type": "payment.succeeded",
            "transaction_reference": txn1,
        }
    ).encode("utf-8")
    hdrs1 = _signed_headers(raw1)

    r1 = api_client.post("/api/v1/webhooks/mock-payment", content=raw1, headers=hdrs1)
    assert r1.status_code == 200
    assert r1.json()["duplicate"] is False
    assert r1.json()["processing_status"] == "processed"

    r2 = api_client.post("/api/v1/webhooks/mock-payment", content=raw1, headers=hdrs1)
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True
    assert r2.json()["processing_status"] == "processed"
    assert r2.json()["payment_status"] == "succeeded"

    db_check1 = session_factory()
    try:
        assert db_check1.get(Invoice, inv1_id).amount_paid == Decimal("90.00")
        assert db_check1.query(WebhookEvent).filter(WebhookEvent.event_id == evt1).count() == 1
    finally:
        db_check1.close()

    # Concurrent 4-thread duplicate delivery for identical event_id
    db_c = session_factory()
    try:
        _, inv2, pay2 = _seed_invoice_and_payment(db_c, invoice_total=Decimal("150.00"), payment_amount=Decimal("150.00"))
        inv2_id = inv2.id
        pay2_id = pay2.id
        txn2 = pay2.transaction_reference
    finally:
        db_c.close()

    evt_conc = f"EVT-CONC-{_uid()}"
    raw_dict = {
        "event_id": evt_conc,
        "event_type": "payment.succeeded",
        "transaction_reference": txn2,
    }
    payload_obj = MockWebhookPayload.model_validate(raw_dict)

    def _deliver():
        wdb = session_factory()
        try:
            res = WebhookService.process_mock_webhook(wdb, payload_obj, raw_dict)
            return res.duplicate, res.processing_status
        finally:
            wdb.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: _deliver(), range(4)))

    dup_flags = [dup for dup, _ in outcomes]
    assert dup_flags.count(False) == 1
    assert dup_flags.count(True) == 3

    db_check2 = session_factory()
    try:
        assert db_check2.get(Invoice, inv2_id).amount_paid == Decimal("150.00")
        assert db_check2.get(Invoice, inv2_id).status == InvoiceStatus.PAID
        assert db_check2.get(Payment, pay2_id).status == PaymentStatus.SUCCEEDED
        assert db_check2.query(WebhookEvent).filter(WebhookEvent.event_id == evt_conc).count() == 1
    finally:
        db_check2.close()


def test_webhook_stale_failure_after_succeeded_and_refunded_never_downgrades(api_client, db_session):
    # Case A: SUCCEEDED payment receiving stale payment.failed
    _, inv_ok, pay_ok = _seed_invoice_and_payment(
        db_session,
        invoice_total=Decimal("70.00"),
        invoice_paid=Decimal("70.00"),
        invoice_status=InvoiceStatus.PAID,
        payment_amount=Decimal("70.00"),
        payment_status=PaymentStatus.SUCCEEDED,
    )
    raw_ok_stale = json.dumps(
        {
            "event_id": f"EVT-STALE-SUCC-{_uid()}",
            "event_type": "payment.failed",
            "transaction_reference": pay_ok.transaction_reference,
            "failure_reason": "Delayed timeout notice",
        }
    ).encode("utf-8")
    r_a = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_ok_stale,
        headers=_signed_headers(raw_ok_stale),
    )
    assert r_a.status_code == 200
    assert r_a.json()["processing_status"] == "ignored_stale"
    assert r_a.json()["payment_status"] == "succeeded"

    # Case B: REFUNDED payment receiving stale payment.failed
    _, inv_ref, pay_ref = _seed_invoice_and_payment(
        db_session,
        invoice_total=Decimal("70.00"),
        invoice_paid=Decimal("70.00"),
        invoice_status=InvoiceStatus.PAID,
        payment_amount=Decimal("70.00"),
        payment_status=PaymentStatus.REFUNDED,
    )
    raw_ref_stale = json.dumps(
        {
            "event_id": f"EVT-STALE-REF-{_uid()}",
            "event_type": "payment.failed",
            "transaction_reference": pay_ref.transaction_reference,
        }
    ).encode("utf-8")
    r_b = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_ref_stale,
        headers=_signed_headers(raw_ref_stale),
    )
    assert r_b.status_code == 200
    assert r_b.json()["processing_status"] == "ignored_stale"
    assert r_b.json()["payment_status"] == "refunded"

    db_session.expire_all()
    assert db_session.get(Payment, pay_ok.id).status == PaymentStatus.SUCCEEDED
    assert db_session.get(Invoice, inv_ok.id).amount_paid == Decimal("70.00")
    assert db_session.get(Payment, pay_ref.id).status == PaymentStatus.REFUNDED
    assert db_session.get(Invoice, inv_ref.id).amount_paid == Decimal("70.00")

    stale_audits = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "webhook.ignored_stale")
        .count()
    )
    assert stale_audits == 2


def test_webhook_invalid_payment_reference_and_invoice_balance_conflict(api_client, db_session):
    # 1. Invalid transaction_reference -> 404
    evt_missing = f"EVT-MISSING-{_uid()}"
    raw_missing = json.dumps(
        {
            "event_id": evt_missing,
            "event_type": "payment.succeeded",
            "transaction_reference": "TXN-DOES-NOT-EXIST",
        }
    ).encode("utf-8")
    r_missing = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_missing,
        headers=_signed_headers(raw_missing),
    )
    assert r_missing.status_code == 404

    ev_missing_db = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == evt_missing).one()
    assert ev_missing_db.processing_status == "rejected"
    assert ev_missing_db.payment_id is None

    # 2. Invoice balance conflict (invoice already PAID before delayed pending payment webhook arrives) -> 409
    _, inv_paid, pay_pending = _seed_invoice_and_payment(
        db_session,
        invoice_total=Decimal("100.00"),
        invoice_paid=Decimal("100.00"),
        invoice_status=InvoiceStatus.PAID,
        payment_amount=Decimal("100.00"),
        payment_status=PaymentStatus.PENDING,
    )
    evt_conflict = f"EVT-CONFLICT-{_uid()}"
    raw_conflict = json.dumps(
        {
            "event_id": evt_conflict,
            "event_type": "payment.succeeded",
            "transaction_reference": pay_pending.transaction_reference,
        }
    ).encode("utf-8")
    r_conflict = api_client.post(
        "/api/v1/webhooks/mock-payment",
        content=raw_conflict,
        headers=_signed_headers(raw_conflict),
    )
    assert r_conflict.status_code == 409

    db_session.expire_all()
    assert db_session.get(Invoice, inv_paid.id).amount_paid == Decimal("100.00")
    assert db_session.get(Payment, pay_pending.id).status == PaymentStatus.PENDING
    ev_conf_db = db_session.query(WebhookEvent).filter(WebhookEvent.event_id == evt_conflict).one()
    assert ev_conf_db.processing_status == "rejected"
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_type == "webhook_event", AuditLog.action == "webhook.invoice_conflict")
        .count()
        == 1
    )


def test_webhook_transaction_rollback_on_failure(session_factory):
    db_seed = session_factory()
    try:
        _, invoice, payment = _seed_invoice_and_payment(
            db_seed,
            invoice_total=Decimal("55.00"),
            payment_amount=Decimal("55.00"),
            payment_status=PaymentStatus.PENDING,
        )
        inv_id = invoice.id
        pay_id = payment.id
        txn_ref = payment.transaction_reference
    finally:
        db_seed.close()

    evt_id = f"EVT-ROLL-{_uid()}"
    raw_dict = {
        "event_id": evt_id,
        "event_type": "payment.succeeded",
        "transaction_reference": txn_ref,
    }
    payload_obj = MockWebhookPayload.model_validate(raw_dict)

    db_fail = session_factory()
    try:
        with patch.object(db_fail, "commit", side_effect=RuntimeError("Simulated webhook DB commit crash")):
            with pytest.raises(RuntimeError, match="Simulated webhook DB commit crash"):
                WebhookService.process_mock_webhook(db_fail, payload_obj, raw_dict)
        db_fail.rollback()
    finally:
        db_fail.close()

    db_verify = session_factory()
    try:
        assert db_verify.get(Payment, pay_id).status == PaymentStatus.PENDING
        assert db_verify.get(Invoice, inv_id).amount_paid == Decimal("0.00")
        assert db_verify.get(Invoice, inv_id).status == InvoiceStatus.OPEN
        assert db_verify.query(WebhookEvent).filter(WebhookEvent.event_id == evt_id).count() == 0
        assert db_verify.query(AuditLog).count() == 0
    finally:
        db_verify.close()


# =====================================================================
# Milestone 2 Task 3 Webhook Tests:
# - POST /webhooks/payment endpoint
# - payment_success, payment_failed, payment_refunded event types
# - Subscription updates (Active remains Active on success, Active -> Past Due on failure)
# - Stale and duplicate webhooks never downgrade subscription
# - Automatic webhook triggering on payment success, failure, and refund
# - Graceful handling of invalid references and payloads
# =====================================================================


def _seed_subscription_and_invoice(
    db,
    *,
    sub_status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
    invoice_total: Decimal = Decimal("100.00"),
    payment_amount: Decimal = Decimal("100.00"),
    payment_status: Optional[PaymentStatus] = PaymentStatus.PENDING,
    create_payment: bool = True,
):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"WHCUST-{tag}",
        full_name=f"Webhook User {tag}",
        email=f"wh.user.{tag.lower()}@example.com",
        currency="USD",
    )
    db.add(customer)
    db.flush()

    plan = Plan(
        name=f"Plan {tag}",
        code=f"PLAN-{tag}",
        price=invoice_total,
        currency="USD",
        billing_interval=BillingInterval.MONTHLY,
    )
    db.add(plan)
    db.flush()

    subscription = Subscription(
        subscription_code=f"SUB-WH-{tag}",
        customer_id=customer.id,
        plan_id=plan.id,
        status=sub_status,
        quantity=1,
        unit_price=invoice_total,
        currency="USD",
        billing_interval=BillingInterval.MONTHLY,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    db.add(subscription)
    db.flush()

    invoice = Invoice(
        invoice_number=f"INV-WH-{tag}",
        customer_id=customer.id,
        subscription_id=subscription.id,
        subtotal=invoice_total,
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=invoice_total,
        amount_paid=Decimal("0.00"),
        currency="USD",
        status=InvoiceStatus.OPEN,
        issue_date=now,
        due_date=now + timedelta(days=7),
    )
    db.add(invoice)
    db.flush()

    payment = None
    if create_payment and payment_status is not None:
        payment = Payment(
            transaction_reference=f"TXN-WH-{tag}",
            invoice_id=invoice.id,
            customer_id=customer.id,
            amount=payment_amount,
            refunded_amount=Decimal("0.00"),
            currency="USD",
            status=payment_status,
            payment_date=now if payment_status in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED) else None,
        )
        db.add(payment)
    db.commit()
    db.refresh(customer)
    db.refresh(subscription)
    db.refresh(invoice)
    if payment is not None:
        db.refresh(payment)
    return customer, subscription, invoice, payment


def test_root_webhook_payment_success_settles_invoice_and_keeps_subscription_active(api_client, db_session):
    customer, subscription, invoice, payment = _seed_subscription_and_invoice(
        db_session,
        sub_status=SubscriptionStatus.ACTIVE,
        invoice_total=Decimal("150.00"),
        payment_amount=Decimal("150.00"),
        payment_status=PaymentStatus.PENDING,
    )

    event_id = f"EVT-SUCC-{_uid()}"
    raw_payload = json.dumps(
        {
            "event_id": event_id,
            "event_type": "payment_success",
            "transaction_reference": payment.transaction_reference,
            "amount": "150.00",
            "currency": "USD",
        }
    ).encode("utf-8")

    resp = api_client.post(
        "/webhooks/payment",
        content=raw_payload,
        headers=_signed_headers(raw_payload),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event_id
    assert body["event_type"] == "payment_success"
    assert body["processing_status"] == "processed"
    assert body["payment_status"] == "succeeded"
    assert body["invoice_status"] == "paid"

    db_session.expire_all()
    p_db = db_session.get(Payment, payment.id)
    i_db = db_session.get(Invoice, invoice.id)
    s_db = db_session.get(Subscription, subscription.id)

    assert p_db.status == PaymentStatus.SUCCEEDED
    assert i_db.status == InvoiceStatus.PAID
    assert i_db.amount_paid == Decimal("150.00")
    # Subscription must remain Active
    assert s_db.status == SubscriptionStatus.ACTIVE


def test_root_webhook_payment_failed_transitions_subscription_to_past_due(api_client, db_session):
    customer, subscription, invoice, payment = _seed_subscription_and_invoice(
        db_session,
        sub_status=SubscriptionStatus.ACTIVE,
        invoice_total=Decimal("200.00"),
        payment_amount=Decimal("200.00"),
        payment_status=PaymentStatus.PENDING,
    )

    event_id = f"EVT-FAIL-{_uid()}"
    raw_payload = json.dumps(
        {
            "event_id": event_id,
            "event_type": "payment_failed",
            "transaction_reference": payment.transaction_reference,
            "failure_reason": "Card declined by issuer",
        }
    ).encode("utf-8")

    resp = api_client.post(
        "/webhooks/payment",
        content=raw_payload,
        headers=_signed_headers(raw_payload),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event_id
    assert body["event_type"] == "payment_failed"
    assert body["processing_status"] == "processed"
    assert body["payment_status"] == "failed"
    assert body["invoice_status"] == "open"
    assert body["subscription_status"] == "past_due"

    db_session.expire_all()
    p_db = db_session.get(Payment, payment.id)
    i_db = db_session.get(Invoice, invoice.id)
    s_db = db_session.get(Subscription, subscription.id)

    assert p_db.status == PaymentStatus.FAILED
    assert i_db.status == InvoiceStatus.OPEN
    assert i_db.amount_paid == Decimal("0.00")
    # Subscription transitions from Active -> Past Due
    assert s_db.status == SubscriptionStatus.PAST_DUE

    sub_audit = (
        db_session.query(AuditLog)
        .filter(
            AuditLog.entity_type == "subscription",
            AuditLog.entity_id == subscription.id,
            AuditLog.action == "subscription.past_due",
        )
        .first()
    )
    assert sub_audit is not None
    assert sub_audit.old_values["status"] == "active"
    assert sub_audit.new_values["status"] == "past_due"


def test_root_webhook_payment_refunded_settles_refund(api_client, db_session):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"WHCUST-{tag}",
        full_name=f"Refund User {tag}",
        email=f"rf.user.{tag.lower()}@example.com",
        currency="USD",
    )
    db_session.add(customer)
    db_session.flush()

    invoice = Invoice(
        invoice_number=f"INV-RF-{tag}",
        customer_id=customer.id,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        amount_paid=Decimal("100.00"),
        currency="USD",
        status=InvoiceStatus.PAID,
        issue_date=now,
        due_date=now + timedelta(days=7),
        paid_at=now,
    )
    db_session.add(invoice)
    db_session.flush()

    payment = Payment(
        transaction_reference=f"TXN-RF-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("100.00"),
        refunded_amount=Decimal("0.00"),
        currency="USD",
        status=PaymentStatus.SUCCEEDED,
        payment_date=now,
    )
    db_session.add(payment)
    db_session.flush()

    refund = PaymentRefund(
        refund_reference=f"REF-RF-{tag}",
        payment_id=payment.id,
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("100.00"),
        currency="USD",
        status=RefundStatus.PENDING,
    )
    db_session.add(refund)
    db_session.commit()

    event_id = f"EVT-REF-{_uid()}"
    raw_payload = json.dumps(
        {
            "event_id": event_id,
            "event_type": "payment_refunded",
            "transaction_reference": payment.transaction_reference,
            "refund_reference": refund.refund_reference,
            "amount": "100.00",
            "currency": "USD",
        }
    ).encode("utf-8")

    resp = api_client.post(
        "/webhooks/payment",
        content=raw_payload,
        headers=_signed_headers(raw_payload),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_id"] == event_id
    assert body["event_type"] == "payment_refunded"
    assert body["processing_status"] == "processed"
    assert body["refund_status"] == "succeeded"

    db_session.expire_all()
    rf_db = db_session.get(PaymentRefund, refund.id)
    p_db = db_session.get(Payment, payment.id)
    i_db = db_session.get(Invoice, invoice.id)

    assert rf_db.status == RefundStatus.SUCCEEDED
    assert p_db.refunded_amount == Decimal("100.00")
    assert p_db.status == PaymentStatus.REFUNDED
    # Historical invoice amount_paid is preserved
    assert i_db.amount_paid == Decimal("100.00")
    assert i_db.status == InvoiceStatus.PAID


def test_automatic_webhook_triggered_on_payment_process_success(api_client, db_session):
    _, _, invoice, _ = _seed_subscription_and_invoice(
        db_session,
        sub_status=SubscriptionStatus.ACTIVE,
        invoice_total=Decimal("80.00"),
        create_payment=False,
    )

    # random_value=0.05 -> deterministic success (< 0.80)
    res = api_client.post(
        "/payments/process",
        json={"invoice_id": invoice.id, "amount": "80.00", "random_value": 0.05},
    )
    assert res.status_code == 201
    pay_data = res.json()
    assert pay_data["status"] == "succeeded"
    txn_ref = pay_data["transaction_reference"]

    # Verify automatic webhook was recorded in database
    db_session.expire_all()
    auto_evt = (
        db_session.query(WebhookEvent)
        .filter(
            WebhookEvent.transaction_reference == txn_ref,
            WebhookEvent.event_type == "payment_success",
        )
        .first()
    )
    assert auto_evt is not None
    assert auto_evt.processing_status == "processed"
    assert auto_evt.signature_verified is True
    assert auto_evt.payload["status"] == "succeeded"


def test_automatic_webhook_triggered_on_payment_process_failure_and_past_due(api_client, db_session):
    _, subscription, invoice, _ = _seed_subscription_and_invoice(
        db_session,
        sub_status=SubscriptionStatus.ACTIVE,
        invoice_total=Decimal("95.00"),
        create_payment=False,
    )

    # random_value=0.95 -> deterministic failure (>= 0.80)
    res = api_client.post(
        "/payments/process",
        json={"invoice_id": invoice.id, "amount": "95.00", "random_value": 0.95},
    )
    assert res.status_code == 201
    pay_data = res.json()
    assert pay_data["status"] == "failed"
    txn_ref = pay_data["transaction_reference"]

    db_session.expire_all()
    # Subscription must have transitioned from Active -> Past Due
    s_db = db_session.get(Subscription, subscription.id)
    assert s_db.status == SubscriptionStatus.PAST_DUE

    # Verify automatic webhook event was recorded in database
    auto_evt = (
        db_session.query(WebhookEvent)
        .filter(
            WebhookEvent.transaction_reference == txn_ref,
            WebhookEvent.event_type == "payment_failed",
        )
        .first()
    )
    assert auto_evt is not None
    assert auto_evt.processing_status == "processed"
    assert auto_evt.signature_verified is True
    assert auto_evt.payload["status"] == "failed"


def test_automatic_webhook_triggered_on_refund_completion(api_client, db_session):
    tag = _uid()
    now = datetime.now(timezone.utc)
    customer = Customer(
        customer_code=f"WHCUST-{tag}",
        full_name=f"Auto Refund User {tag}",
        email=f"auto.rf.{tag.lower()}@example.com",
        currency="USD",
    )
    db_session.add(customer)
    db_session.flush()

    invoice = Invoice(
        invoice_number=f"INV-AUTORF-{tag}",
        customer_id=customer.id,
        subtotal=Decimal("50.00"),
        tax_amount=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        total_amount=Decimal("50.00"),
        amount_paid=Decimal("50.00"),
        currency="USD",
        status=InvoiceStatus.PAID,
        issue_date=now,
        due_date=now + timedelta(days=7),
        paid_at=now,
    )
    db_session.add(invoice)
    db_session.flush()

    payment = Payment(
        transaction_reference=f"TXN-AUTORF-{tag}",
        invoice_id=invoice.id,
        customer_id=customer.id,
        amount=Decimal("50.00"),
        refunded_amount=Decimal("0.00"),
        currency="USD",
        status=PaymentStatus.SUCCEEDED,
        payment_date=now,
    )
    db_session.add(payment)
    db_session.commit()

    # Call refund endpoint
    resp = api_client.post(
        f"/api/v1/payments/{payment.id}/refund",
        json={"amount": "50.00", "reason": "Customer cancellation"},
    )
    assert resp.status_code == 201
    refund_data = resp.json()
    assert refund_data["status"] == "succeeded"
    rf_ref = refund_data["refund_reference"]

    # Verify automatic refund webhook was recorded in database
    db_session.expire_all()
    auto_evt = (
        db_session.query(WebhookEvent)
        .filter(
            WebhookEvent.transaction_reference == payment.transaction_reference,
            WebhookEvent.event_type == "payment_refunded",
        )
        .first()
    )
    assert auto_evt is not None
    assert auto_evt.processing_status == "processed"
    assert auto_evt.payload["refund_reference"] == rf_ref


def test_stale_and_duplicate_failure_webhooks_never_downgrade_subscription(api_client, db_session):
    _, subscription, invoice, payment = _seed_subscription_and_invoice(
        db_session,
        sub_status=SubscriptionStatus.ACTIVE,
        invoice_total=Decimal("60.00"),
        payment_amount=Decimal("60.00"),
        payment_status=PaymentStatus.SUCCEEDED,
    )

    event_id = f"EVT-STALE-{_uid()}"
    raw_payload = json.dumps(
        {
            "event_id": event_id,
            "event_type": "payment_failed",
            "transaction_reference": payment.transaction_reference,
            "failure_reason": "Belated gateway error",
        }
    ).encode("utf-8")

    # 1. Stale failure arriving for a SUCCEEDED payment
    resp1 = api_client.post(
        "/webhooks/payment",
        content=raw_payload,
        headers=_signed_headers(raw_payload),
    )
    assert resp1.status_code == 200
    assert resp1.json()["processing_status"] == "ignored_stale"
    assert resp1.json()["payment_status"] == "succeeded"

    db_session.expire_all()
    # Subscription must NOT be downgraded!
    assert db_session.get(Subscription, subscription.id).status == SubscriptionStatus.ACTIVE
    assert db_session.get(Payment, payment.id).status == PaymentStatus.SUCCEEDED

    # 2. Duplicate delivery of the same webhook
    resp2 = api_client.post(
        "/webhooks/payment",
        content=raw_payload,
        headers=_signed_headers(raw_payload),
    )
    assert resp2.status_code == 200
    assert resp2.json()["duplicate"] is True
    assert db_session.get(Subscription, subscription.id).status == SubscriptionStatus.ACTIVE


def test_webhook_payment_endpoint_invalid_payload_and_missing_reference(api_client, db_session):
    # 1. Invalid signature -> 401
    bad_sig_headers = {"Content-Type": "application/json", "X-Mock-Signature": "t=123,v1=invalid"}
    res_401 = api_client.post(
        "/webhooks/payment",
        content=b'{"event_id": "EVT-1", "event_type": "payment_success"}',
        headers=bad_sig_headers,
    )
    assert res_401.status_code == 401

    # 2. Missing required transaction reference -> 422
    payload_no_ref = json.dumps(
        {
            "event_id": f"EVT-NOREF-{_uid()}",
            "event_type": "payment_success",
        }
    ).encode("utf-8")
    res_422 = api_client.post(
        "/webhooks/payment",
        content=payload_no_ref,
        headers=_signed_headers(payload_no_ref),
    )
    assert res_422.status_code == 422

    # 3. Non-existent payment reference -> 404
    payload_404 = json.dumps(
        {
            "event_id": f"EVT-404-{_uid()}",
            "event_type": "payment_success",
            "transaction_reference": "TXN-NONEXISTENT-9999",
        }
    ).encode("utf-8")
    res_404 = api_client.post(
        "/webhooks/payment",
        content=payload_404,
        headers=_signed_headers(payload_404),
    )
    assert res_404.status_code == 404


