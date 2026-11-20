from datetime import datetime
from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    Integer,
    String,
    Text,
    Numeric,
    DateTime,
    ForeignKey,
    Enum as SQLEnum,
    UniqueConstraint,
    CheckConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base
from app.models.enums import InvoiceItemType, InvoiceStatus, PaymentStatus, RefundStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.subscription import Subscription
    from app.models.billing_cycle import BillingCycle
    from app.models.payment import Payment, PaymentRefund


class Invoice(Base):
    """
    Represents a formal billing statement generated for a Customer's Subscription
    or manual charge, linked to a BillingCycle for idempotency.
    Financial history is protected via RESTRICT foreign keys.
    """
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("invoice_number", name="uq_invoices_invoice_number"),
        UniqueConstraint("billing_cycle_id", name="uq_invoices_billing_cycle_id"),
        CheckConstraint("subtotal >= 0", name="ck_invoices_subtotal_non_negative"),
        CheckConstraint("tax_amount >= 0", name="ck_invoices_tax_amount_non_negative"),
        CheckConstraint("discount_amount >= 0", name="ck_invoices_discount_amount_non_negative"),
        CheckConstraint("total_amount >= 0", name="ck_invoices_total_amount_non_negative"),
        CheckConstraint("amount_paid >= 0", name="ck_invoices_amount_paid_non_negative"),
        CheckConstraint("amount_paid <= total_amount", name="ck_invoices_amount_paid_within_total"),
        CheckConstraint("due_date >= issue_date", name="ck_invoices_due_date_valid"),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_invoices_currency_iso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    invoice_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    billing_cycle_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("billing_cycles.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    status: Mapped[InvoiceStatus] = mapped_column(
        SQLEnum(
            InvoiceStatus,
            name="invoice_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=InvoiceStatus.OPEN,
        index=True,
    )

    issue_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    due_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    customer: Mapped["Customer"] = relationship("Customer", back_populates="invoices")
    subscription: Mapped[Optional["Subscription"]] = relationship("Subscription", back_populates="invoices")
    billing_cycle: Mapped[Optional["BillingCycle"]] = relationship("BillingCycle", back_populates="invoice")
    items: Mapped[List["InvoiceItem"]] = relationship(
        "InvoiceItem",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="invoice",
        passive_deletes=True,
    )
    refunds: Mapped[List["PaymentRefund"]] = relationship(
        "PaymentRefund",
        back_populates="invoice",
        passive_deletes=True,
    )

    @property
    def total_succeeded_payments(self) -> Decimal:
        """Historical sum of all settled payment amounts (succeeded or subsequently refunded)."""
        return sum(
            (p.amount for p in self.payments if p.status in (PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED)),
            Decimal("0.00"),
        )

    @property
    def total_refunded_amount(self) -> Decimal:
        """Total successful refunds recorded against payments on this invoice."""
        return sum(
            (r.amount for r in self.refunds if r.status == RefundStatus.SUCCEEDED),
            Decimal("0.00"),
        )

    @property
    def net_collected_amount(self) -> Decimal:
        """Net collected revenue (successful payments minus successful refunds)."""
        return (self.total_succeeded_payments - self.total_refunded_amount).quantize(Decimal("0.01"))

    @property
    def customer_name(self) -> Optional[str]:
        return self.customer.full_name if self.customer else None

    @property
    def customer_email(self) -> Optional[str]:
        return self.customer.email if self.customer else None

    @property
    def plan_name(self) -> Optional[str]:
        if self.subscription and self.subscription.plan:
            return self.subscription.plan.name
        return None

    @property
    def plan_code(self) -> Optional[str]:
        if self.subscription and self.subscription.plan:
            return self.subscription.plan.code
        return None

    def __repr__(self) -> str:
        return (
            f"<Invoice(id={self.id}, number='{self.invoice_number}', "
            f"total={self.total_amount} {self.currency}, status='{self.status.value}')>"
        )


class InvoiceItem(Base):
    """
    Supporting normalized table representing individual itemized charges on an Invoice
    (e.g., recurring base charge, proration adjustment, tax line, add-ons).
    """
    __tablename__ = "invoice_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_invoice_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_invoice_items_unit_price_non_negative"),
        CheckConstraint("amount >= 0", name="ck_invoice_items_amount_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    invoice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_type: Mapped[InvoiceItemType] = mapped_column(
        SQLEnum(
            InvoiceItemType,
            name="invoice_item_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=InvoiceItemType.BASE_SUBSCRIPTION,
        server_default=text("'base_subscription'"),
        index=True,
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")

    def __repr__(self) -> str:
        return f"<InvoiceItem(id={self.id}, invoice_id={self.invoice_id}, amount={self.amount})>"
