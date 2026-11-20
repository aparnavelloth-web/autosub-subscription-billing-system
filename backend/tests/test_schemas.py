"""
Unit tests for Phase 3 Pydantic v2 Schemas across all domain entities:
Plan, Customer, Subscription, BillingCycle, Invoice, Payment.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.models.enums import (
    BillingCycleStatus,
    BillingInterval,
    InvoiceStatus,
    PaymentMethod,
    PaymentStatus,
    SubscriptionStatus,
)
from app.schemas import (
    BillingCycleCreate,
    CustomerCreate,
    InvoiceCreate,
    InvoiceItemCreate,
    PaymentCreate,
    PlanCreate,
    PlanUpdate,
    SubscriptionCreate,
)


def test_plan_schema_validation_and_normalization():
    plan = PlanCreate(
        name="  Growth Plan  ",
        code=" growth_monthly ",
        price=Decimal("49.99"),
        currency="usd",
        billing_interval=BillingInterval.MONTHLY,
        trial_days=14,
        features=["API Access", "Priority Support"],
    )
    assert plan.name == "Growth Plan"
    assert plan.code == "GROWTH_MONTHLY"
    assert plan.currency == "USD"
    assert plan.trial_days == 14

    # Negative price rejected
    with pytest.raises(ValidationError):
        PlanCreate(name="Bad", code="BAD", price=Decimal("-1.00"))

    # Negative price in update rejected
    with pytest.raises(ValidationError):
        PlanUpdate(price=Decimal("-0.01"))

    # Invalid currency rejected
    with pytest.raises(ValidationError):
        PlanCreate(name="BadCurr", code="BADCUR", price=Decimal("10.00"), currency="US1")

    # Negative trial days rejected
    with pytest.raises(ValidationError):
        PlanCreate(name="BadTrial", code="BADTR", price=Decimal("10.00"), trial_days=-5)


def test_customer_schema_validation_and_normalization():
    cust = CustomerCreate(
        customer_code=" cust-001 ",
        full_name="  Priya Nair ",
        email="  Priya.Nair@Example.COM ",
        currency="inr",
    )
    assert cust.customer_code == "CUST-001"
    assert cust.full_name == "Priya Nair"
    assert cust.email == "priya.nair@example.com"
    assert cust.currency == "INR"

    with pytest.raises(ValidationError):
        CustomerCreate(full_name="Bad Email", email="not-an-email")


def test_subscription_schema_validation():
    now = datetime.now(timezone.utc)
    sub = SubscriptionCreate(
        customer_id=1,
        plan_id=2,
        quantity=3,
        status=SubscriptionStatus.TRIAL,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
    )
    assert sub.status == SubscriptionStatus.TRIAL
    assert sub.quantity == 3

    # End <= Start rejected
    with pytest.raises(ValidationError):
        SubscriptionCreate(
            customer_id=1,
            plan_id=2,
            current_period_start=now,
            current_period_end=now - timedelta(days=1),
        )

    # Zero quantity rejected
    with pytest.raises(ValidationError):
        SubscriptionCreate(customer_id=1, plan_id=2, quantity=0)


def test_billing_cycle_schema_validation():
    now = datetime.now(timezone.utc)
    cycle = BillingCycleCreate(
        subscription_id=1,
        cycle_number=1,
        period_start=now,
        period_end=now + timedelta(days=30),
        billing_date=now,
        amount=Decimal("29.00"),
        status=BillingCycleStatus.PENDING,
    )
    assert cycle.cycle_number == 1

    with pytest.raises(ValidationError):
        BillingCycleCreate(
            subscription_id=1,
            cycle_number=1,
            period_start=now,
            period_end=now,
            billing_date=now,
            amount=Decimal("29.00"),
        )


def test_invoice_schema_validation():
    now = datetime.now(timezone.utc)
    inv = InvoiceCreate(
        customer_id=1,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("18.00"),
        discount_amount=Decimal("10.00"),
        total_amount=Decimal("108.00"),
        currency="USD",
        issue_date=now,
        due_date=now + timedelta(days=7),
        status=InvoiceStatus.OPEN,
        items=[
            InvoiceItemCreate(
                description="Pro Plan Monthly",
                quantity=1,
                unit_price=Decimal("100.00"),
                amount=Decimal("100.00"),
            )
        ],
    )
    assert len(inv.items) == 1

    # Negative tax rejected
    with pytest.raises(ValidationError):
        InvoiceCreate(
            customer_id=1,
            tax_amount=Decimal("-5.00"),
            total_amount=Decimal("95.00"),
            due_date=now + timedelta(days=7),
        )

    # due_date < issue_date rejected
    with pytest.raises(ValidationError):
        InvoiceCreate(
            customer_id=1,
            total_amount=Decimal("100.00"),
            issue_date=now,
            due_date=now - timedelta(days=1),
        )


def test_payment_schema_validation():
    pay = PaymentCreate(
        invoice_id=10,
        customer_id=1,
        amount=Decimal("108.00"),
        currency="USD",
        payment_method=PaymentMethod.CREDIT_CARD,
    )
    assert pay.amount == Decimal("108.00")

    # Payment amount must be strictly positive
    with pytest.raises(ValidationError):
        PaymentCreate(invoice_id=10, customer_id=1, amount=Decimal("0.00"))

