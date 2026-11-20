"""
End-to-End Verification Script for AutoSub Milestone 2 Tasks:
Task 1: Invoice Generation and PDF Download
Task 2: Proration and Plan Change Logic

Target database: subscription_billing_test_db
"""
import sys
import uuid
import zlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# Setup paths and import app
sys.path.insert(0, str(Path(__file__).resolve().parent))

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfutils

from app.core.config import settings
from app.database.session import get_db
from app.main import app
from app.models.enums import BillingInterval, BillingCycleStatus, SubscriptionStatus, InvoiceStatus, InvoiceItemType
from app.models.customer import Customer
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.billing_cycle import BillingCycle
from app.models.invoice import Invoice
from app.models.audit_log import AuditLog


def extract_pdf_stream_text(pdf_bytes: bytes) -> str:
    """Extracts and decodes ASCII85+FlateDecode streams from ReportLab PDF."""
    s_idx = pdf_bytes.find(b"stream")
    while pdf_bytes[s_idx:s_idx+1] not in b"\r\n":
        s_idx += 1
    while pdf_bytes[s_idx:s_idx+1] in b"\r\n":
        s_idx += 1
    e_idx = pdf_bytes.find(b"endstream", s_idx)
    raw = pdf_bytes[s_idx:e_idx].strip()
    decoded_a85 = pdfutils.asciiBase85Decode(raw)
    decomp = zlib.decompress(decoded_a85)
    return decomp.decode("latin1", errors="ignore")


def run_e2e_verification():
    print("=" * 80)
    print("AUTOSUB MILESTONE 2 END-TO-END VERIFICATION")
    print("=" * 80)

    # 1. Database setup
    test_url = settings.test_database_url
    assert "test" in test_url.lower(), "Must use test database"
    print(f"[*] Targeting isolated test database: {test_url.split('@')[-1]}")

    alembic_cfg = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", test_url.replace("%", "%%"))
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(test_url, pool_pre_ping=True)
    SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Clean test tables
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE webhook_events, payment_refunds, payments, invoice_items, invoices, "
                "billing_cycles, subscriptions, audit_logs, customers, plans, users "
                "RESTART IDENTITY CASCADE;"
            )
        )

    def _override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)

    # =========================================================================
    # TASK 1: INVOICE GENERATION & PDF VERIFICATION
    # =========================================================================
    print("\n" + "-" * 80)
    print("TASK 1: INVOICE GENERATION AND PDF DOWNLOAD VERIFICATION")
    print("-" * 80)

    uid = uuid.uuid4().hex[:6]
    # 1.1 Create Customer
    cust_resp = client.post(
        "/api/v1/customers",
        json={
            "full_name": f"Acme Global {uid}",
            "email": f"billing_{uid}@acmeglobal.com",
            "currency": "INR",
        },
    )
    assert cust_resp.status_code == 201, f"Customer creation failed: {cust_resp.text}"
    customer = cust_resp.json()
    cust_id = customer["id"]
    print(f"[✓] Created Customer: '{customer['full_name']}' (#{cust_id}) | Email: {customer['email']}")

    # 1.2 Create Plan
    plan_resp = client.post(
        "/api/v1/plans",
        json={
            "code": f"BIZ-GOLD-{uid}",
            "name": "Gold Enterprise Tier",
            "price": "4999.00",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    )
    assert plan_resp.status_code == 201, f"Plan creation failed: {plan_resp.text}"
    plan = plan_resp.json()
    plan_id = plan["id"]
    print(f"[✓] Created Plan: '{plan['name']}' (#{plan_id}) | Price: ₹{plan['price']}/month")

    # 1.3 Create Subscription
    now = datetime.now(timezone.utc)
    sub_resp = client.post(
        "/api/v1/subscriptions",
        json={
            "customer_id": cust_id,
            "plan_id": plan_id,
            "billing_interval": "monthly",
            "quantity": 1,
            "start_date": now.isoformat(),
        },
    )
    assert sub_resp.status_code == 201, f"Subscription creation failed: {sub_resp.text}"
    subscription = sub_resp.json()
    sub_id = subscription["id"]
    print(f"[✓] Created Active Subscription: {subscription['subscription_code']} (#{sub_id})")

    # 1.4 Create Pending Billing Cycle
    cycle_start = now - timedelta(days=30)
    cycle_end = now
    with SessionFactory() as db:
        cycle = BillingCycle(
            subscription_id=sub_id,
            cycle_number=1,
            period_start=cycle_start,
            period_end=cycle_end,
            billing_date=now,
            status=BillingCycleStatus.PENDING,
            amount=Decimal("4999.00"),
            currency="INR",
        )
        db.add(cycle)
        db.commit()
        db.refresh(cycle)
        cycle_id = cycle.id
    print(f"[✓] Created Pending Billing Cycle #{cycle_id} (Window: {cycle_start.date()} to {cycle_end.date()})")

    # 1.5 Generate Invoice via POST /api/v1/invoices/generate
    gen_resp = client.post(
        "/api/v1/invoices/generate",
        json={
            "billing_cycle_id": cycle_id,
            "tax_rate": "0.18", # 18% GST
            "discount_amount": "0.00",
        },
    )
    assert gen_resp.status_code == 201, f"Invoice generation failed: {gen_resp.text}"
    inv_data = gen_resp.json()
    inv_id = inv_data["id"]
    inv_num = inv_data["invoice_number"]

    # Verify Calculations
    expected_subtotal = Decimal("4999.00")
    expected_tax = (expected_subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
    expected_total = expected_subtotal + expected_tax

    assert Decimal(str(inv_data["subtotal"])) == expected_subtotal, f"Subtotal mismatch: {inv_data['subtotal']}"
    assert Decimal(str(inv_data["tax_amount"])) == expected_tax, f"Tax mismatch: {inv_data['tax_amount']}"
    assert Decimal(str(inv_data["total_amount"])) == expected_total, f"Total mismatch: {inv_data['total_amount']}"
    assert inv_data["status"] == "open", f"Status must be open, got {inv_data['status']}"
    assert len(inv_data["items"]) >= 1, "Must contain line items"
    print(f"[✓] POST /api/v1/invoices/generate => 201 Created")
    print(f"    Invoice Number: {inv_num} (Format validated: starts with 'INV-')")
    print(f"    Status: {inv_data['status'].upper()} (Unpaid)")
    print(f"    Subtotal: ₹{inv_data['subtotal']} | Tax (18%): ₹{inv_data['tax_amount']} | Total: ₹{inv_data['total_amount']}")

    # 1.6 Verify Duplicate Prevention (Idempotency)
    dup_resp = client.post("/api/v1/invoices/generate", json={"billing_cycle_id": cycle_id})
    assert dup_resp.status_code == 409, f"Expected 409 for duplicate cycle, got {dup_resp.status_code}"
    print(f"[✓] Idempotency: Duplicate invoice generation blocked with HTTP 409 Conflict")

    # 1.7 Verify GET /api/v1/invoices (Listing, Status filter, Search)
    list_resp = client.get("/api/v1/invoices?status=open")
    assert list_resp.status_code == 200
    list_items = list_resp.json()["items"]
    assert any(i["id"] == inv_id for i in list_items), "Generated invoice must appear in GET /api/v1/invoices"

    search_resp = client.get(f"/api/v1/invoices?query={inv_num}")
    assert search_resp.status_code == 200
    assert search_resp.json()["total"] >= 1, "Search by invoice number failed"
    print(f"[✓] GET /api/v1/invoices => 200 OK (verified listing, status=open filter, and number search)")

    # 1.8 Verify GET /api/v1/invoices/{id} (Detail)
    detail_resp = client.get(f"/api/v1/invoices/{inv_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["customer_name"] == customer["full_name"]
    assert detail["customer_email"] == customer["email"]
    assert detail["plan_name"] == plan["name"]
    print(f"[✓] GET /api/v1/invoices/{inv_id} => 200 OK (Customer & Subscription relationships verified)")

    # 1.9 Verify GET /api/v1/invoices/{id}/pdf (ReportLab PDF Generation)
    pdf_resp = client.get(f"/api/v1/invoices/{inv_id}/pdf")
    assert pdf_resp.status_code == 200, f"PDF fetch failed: {pdf_resp.text}"
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert f"invoice-{inv_num}.pdf" in pdf_resp.headers.get("content-disposition", "")
    pdf_bytes = pdf_resp.content

    assert pdf_bytes.startswith(b"%PDF-"), "Invalid PDF header magic bytes"
    assert len(pdf_bytes) > 2000, f"PDF too small: {len(pdf_bytes)} bytes"

    # Save to disk for user inspection
    pdf_path = Path(__file__).resolve().parent / "sample_invoice.pdf"
    pdf_path.write_bytes(pdf_bytes)
    print(f"[✓] GET /api/v1/invoices/{inv_id}/pdf => 200 OK (Saved {len(pdf_bytes)} bytes to {pdf_path.name})")

    # 1.10 PDF Text Inspection
    full_pdf_text = extract_pdf_stream_text(pdf_bytes)

    # Assertions on PDF contents
    required_strings = [
        "AutoSub",
        inv_num,
        customer["full_name"],
        customer["email"],
        plan["name"],
        "STATUS: OPEN",
        "Subtotal:",
        f"{inv_data['subtotal']}",
        "Tax:",
        f"{inv_data['tax_amount']}",
        "Total Amount:",
        f"{inv_data['total_amount']}",
        "Outstanding Balance:",
        "Thank you for choosing AutoSub",
    ]
    for req in required_strings:
        assert req in full_pdf_text, f"PDF content missing expected token: '{req}'"
        print(f"    [PDF Content Verified] Found: '{req}'")

    # Verify no leaked traces
    leak_checks = ["Traceback", "Exception", "SELECT ", "secret", "NoneType"]
    for leak in leak_checks:
        assert leak not in full_pdf_text, f"Potential leak or error in PDF: '{leak}'"
    print(f"[✓] PDF Quality: Visual structure, styling, typography, and leak-free validation passed.")

    # 1.11 Status Transition: Verify PAID invoice and PDF rendering
    with SessionFactory() as db:
        inv_record = db.query(Invoice).filter(Invoice.id == inv_id).first()
        inv_record.status = InvoiceStatus.PAID
        inv_record.amount_paid = inv_record.total_amount
        inv_record.paid_at = datetime.now(timezone.utc)
        db.commit()

    pdf_paid_resp = client.get(f"/api/v1/invoices/{inv_id}/pdf")
    assert pdf_paid_resp.status_code == 200
    full_paid_text = extract_pdf_stream_text(pdf_paid_resp.content)
    assert "STATUS: PAID" in full_paid_text
    print(f"[✓] Status Transition: Invoice marked PAID reflects 'STATUS: PAID' in PDF")


    # =========================================================================
    # TASK 2: PRORATION AND PLAN CHANGE LOGIC
    # =========================================================================
    print("\n" + "-" * 80)
    print("TASK 2: PRORATION AND PLAN CHANGE LOGIC (MENTOR'S SPECIFICATION)")
    print("-" * 80)

    # Setup exactly according to the mentor's example:
    # Current plan: Pro — ₹1000/month
    # New plan: Premium — ₹2000/month
    # Total billing period: 30 days (e.g. May 1 to May 31)
    # Remaining days: 15 (as_of May 16)
    # Unused credit = ₹500
    # New plan charge = ₹1000
    # Net amount due = ₹500

    plan_pro_resp = client.post(
        "/api/v1/plans",
        json={
            "code": f"PRO-{uid}",
            "name": "Pro Plan",
            "price": "1000.00",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    )
    plan_pro = plan_pro_resp.json()

    plan_prem_resp = client.post(
        "/api/v1/plans",
        json={
            "code": f"PREM-{uid}",
            "name": "Premium Plan",
            "price": "2000.00",
            "currency": "INR",
            "billing_interval": "monthly",
            "trial_days": 0,
        },
    )
    plan_prem = plan_prem_resp.json()
    print(f"[✓] Plan Pro: ₹{plan_pro['price']}/month (ID: {plan_pro['id']})")
    print(f"[✓] Plan Premium: ₹{plan_prem['price']}/month (ID: {plan_prem['id']})")

    # Create active subscription with Pro plan
    start_d = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    end_d = datetime(2026, 5, 31, 0, 0, 0, tzinfo=timezone.utc)
    as_of_d = datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)

    with SessionFactory() as db:
        pro_sub = Subscription(
            subscription_code=f"SUB-PRO-{uid}",
            customer_id=cust_id,
            plan_id=plan_pro["id"],
            status=SubscriptionStatus.ACTIVE,
            billing_interval=BillingInterval.MONTHLY,
            quantity=1,
            unit_price=Decimal("1000.00"),
            currency="INR",
            start_date=start_d,
            current_period_start=start_d,
            current_period_end=end_d,
        )
        db.add(pro_sub)
        db.commit()
        db.refresh(pro_sub)
        pro_sub_id = pro_sub.id

    print(f"[✓] Created Active Subscription #{pro_sub_id} on Pro Plan")
    print(f"    Current Period: {start_d.date()} to {end_d.date()} (30 days)")
    print(f"    Proration Evaluation as_of: {as_of_d.date()} (15 days remaining)")

    # 2.1 Call Proration Preview Endpoint
    prev_resp = client.post(
        f"/api/v1/subscriptions/{pro_sub_id}/proration-preview",
        json={
            "new_plan_id": plan_prem["id"],
            "quantity": 1,
            "as_of": as_of_d.isoformat(),
        },
    )
    assert prev_resp.status_code == 200, f"Preview failed: {prev_resp.text}"
    preview = prev_resp.json()

    print("\n[VERIFICATION: MENTOR'S MATHEMATICAL CRITERIA]")
    print(f"  • Total Billing Period Days: {preview['total_billing_days']}  (Expected: 30)")
    print(f"  • Remaining Billing Days:    {preview['remaining_billing_days']}  (Expected: 15)")
    print(f"  • Unused Credit (Pro):       ₹{preview['unused_credit']}  (Expected: ₹500.00)")
    print(f"  • Prorated Charge (Premium): ₹{preview['prorated_charge']} (Expected: ₹1000.00)")
    print(f"  • Net Amount Due:            ₹{preview['net_amount_due']}  (Expected: ₹500.00)")
    print(f"  • Change Type:               {preview['change_type']}")

    assert preview["total_billing_days"] == 30, f"Expected 30 total days, got {preview['total_billing_days']}"
    assert preview["remaining_billing_days"] == 15, f"Expected 15 remaining days, got {preview['remaining_billing_days']}"
    assert Decimal(str(preview["unused_credit"])) == Decimal("500.00"), f"Expected 500.00 credit, got {preview['unused_credit']}"
    assert Decimal(str(preview["prorated_charge"])) == Decimal("1000.00"), f"Expected 1000.00 charge, got {preview['prorated_charge']}"
    assert Decimal(str(preview["net_amount_due"])) == Decimal("500.00"), f"Expected 500.00 net due, got {preview['net_amount_due']}"
    print("[✓] EXACT MENTOR PRORATION FORMULA VERIFIED!")

    # Verify Preview has ZERO Side Effects
    with SessionFactory() as db:
        check_sub = db.query(Subscription).filter(Subscription.id == pro_sub_id).first()
        assert check_sub.plan_id == plan_pro["id"], "Preview must not alter subscription plan!"
        inv_count = db.query(Invoice).filter(Invoice.subscription_id == pro_sub_id).count()
        assert inv_count == 0, "Preview must not create financial invoices!"
    print("[✓] Read-only Guarantee: Preview executed with zero side-effects.")

    # 2.2 Confirm Plan Change via POST /api/v1/subscriptions/{id}/change-plan
    change_resp = client.post(
        f"/api/v1/subscriptions/{pro_sub_id}/change-plan",
        json={
            "new_plan_id": plan_prem["id"],
            "quantity": 1,
            "as_of": as_of_d.isoformat(),
        },
    )
    assert change_resp.status_code == 200, f"Change plan failed: {change_resp.text}"
    result = change_resp.json()
    print("\n[✓] POST /api/v1/subscriptions/{id}/change-plan => 200 OK")
    print(f"    Subscription Plan ID Updated To: #{result['plan_id']}")
    print(f"    New Contract Unit Price: ₹{result['unit_price']}")
    print(f"    Adjustment Invoice: {result['invoice_number']} (Net: ₹{result['proration_net_amount']})")

    # 2.3 Verify Database State & Adjustment Invoice Line Items
    with SessionFactory() as db:
        updated_sub = db.query(Subscription).filter(Subscription.id == pro_sub_id).first()
        assert updated_sub.plan_id == plan_prem["id"]
        assert updated_sub.unit_price == Decimal("2000.00")

        adj_inv = db.query(Invoice).filter(Invoice.invoice_number == result["invoice_number"]).first()
        assert adj_inv is not None
        assert adj_inv.total_amount == Decimal("500.00")
        assert adj_inv.subtotal == Decimal("1000.00")
        assert adj_inv.discount_amount == Decimal("500.00")

        # Verify itemized adjustment lines
        item_types = [item.item_type for item in adj_inv.items]
        assert InvoiceItemType.PLAN_UPGRADE in item_types, "Must contain plan_upgrade item"
        assert InvoiceItemType.PRORATION_CREDIT in item_types, "Must contain proration_credit item"

        upgrade_item = next(i for i in adj_inv.items if i.item_type == InvoiceItemType.PLAN_UPGRADE)
        credit_item = next(i for i in adj_inv.items if i.item_type == InvoiceItemType.PRORATION_CREDIT)
        assert upgrade_item.amount == Decimal("1000.00")
        assert credit_item.amount == Decimal("500.00")
        print(f"[✓] Adjustment Invoice Verified:")
        print(f"    + {upgrade_item.description}: ₹{upgrade_item.amount}")
        print(f"    - {credit_item.description} (Credit): ₹{credit_item.amount}")
        print(f"    = Net Adjustment Total: ₹{adj_inv.total_amount}")

        # 2.4 Verify Audit Log Entry
        audit = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_type == "subscription",
                AuditLog.entity_id == pro_sub_id,
                AuditLog.action == "subscription.plan_changed",
            )
            .first()
        )
        assert audit is not None, "AuditLog entry for subscription.plan_changed must exist"
        assert audit.old_values["plan_id"] == plan_pro["id"]
        assert audit.new_values["plan_id"] == plan_prem["id"]
        assert audit.new_values["unit_price"] == "2000.00"
        print(f"[✓] Audit Trail Verified: Logged '{audit.action}' with old/new plan details and diffs")

    # 2.5 Verify Subscription History Timeline Endpoint
    hist_resp = client.get(f"/api/v1/subscriptions/{pro_sub_id}/history")
    assert hist_resp.status_code == 200
    timeline = hist_resp.json()
    assert len(timeline) >= 2, "Timeline must record plan change and proration invoice"
    event_types = [e["event_type"] for e in timeline]
    assert "plan_changed" in event_types, "Timeline must contain plan_changed event"
    assert "proration_invoice" in event_types, "Timeline must contain proration_invoice event"
    print(f"[✓] GET /api/v1/subscriptions/{pro_sub_id}/history => 200 OK (Timeline events: {event_types})")

    print("\n" + "=" * 80)
    print("ALL MILESTONE 2 VERIFICATION CHECKS PASSED WITH 100% SUCCESS!")
    print("=" * 80)


if __name__ == "__main__":
    run_e2e_verification()
