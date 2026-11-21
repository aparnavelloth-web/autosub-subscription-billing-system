from datetime import datetime
from decimal import Decimal
from typing import Optional, Any, List, TYPE_CHECKING
from sqlalchemy import (
    Integer,
    String,
    Text,
    Numeric,
    Boolean,
    DateTime,
    ForeignKey,
    Enum as SQLEnum,
    UniqueConstraint,
    CheckConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base
from app.models.enums import PaymentStatus, PaymentMethod, RefundStatus

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.customer import Customer


class Payment(Base):
    """
    Represents a payment attempt or settled transaction against an Invoice.
    Protected against deletion via RESTRICT foreign keys.
    Partial refunds keep status='succeeded' and increment refunded_amount;
    status transitions to 'refunded' only when refunded_amount == amount.
    """
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("transaction_reference", name="uq_payments_transaction_reference"),
        UniqueConstraint("idempotency_key", name="uq_payments_idempotency_key"),
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint(
            "refunded_amount >= 0 AND refunded_amount <= amount",
            name="ck_payments_refunded_amount_valid",
        ),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_payments_currency_iso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    transaction_reference: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    invoice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    refunded_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    payment_method: Mapped[PaymentMethod] = mapped_column(
        SQLEnum(
            PaymentMethod,
            name="payment_method_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=PaymentMethod.SIMULATED,
    )
    status: Mapped[PaymentStatus] = mapped_column(
        SQLEnum(
            PaymentStatus,
            name="payment_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
    )

    payment_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    gateway_response: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

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
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payments")
    customer: Mapped["Customer"] = relationship("Customer", back_populates="payments")
    refunds: Mapped[List["PaymentRefund"]] = relationship(
        "PaymentRefund",
        back_populates="payment",
        passive_deletes=True,
    )
    webhook_events: Mapped[List["WebhookEvent"]] = relationship(
        "WebhookEvent",
        back_populates="payment",
        passive_deletes=True,
    )

    @property
    def remaining_refundable_amount(self) -> Decimal:
        """Amount remaining available for partial or full refund."""
        if self.status != PaymentStatus.SUCCEEDED:
            return Decimal("0.00")
        remaining = (self.amount or Decimal("0.00")) - (self.refunded_amount or Decimal("0.00"))
        return max(Decimal("0.00"), remaining)

    @property
    def invoice_number(self) -> Optional[str]:
        return self.invoice.invoice_number if self.invoice else None

    @property
    def customer_name(self) -> Optional[str]:
        return self.customer.full_name if self.customer else None

    @property
    def customer_email(self) -> Optional[str]:
        return self.customer.email if self.customer else None

    def __repr__(self) -> str:
        return (
            f"<Payment(id={self.id}, ref='{self.transaction_reference}', "
            f"amount={self.amount} {self.currency}, refunded={self.refunded_amount}, status='{self.status.value}')>"
        )


class PaymentRefund(Base):
    """
    Immutable financial ledger entry recording an individual partial or full refund
    against a settled Payment. Protected via RESTRICT foreign keys.
    """
    __tablename__ = "payment_refunds"
    __table_args__ = (
        UniqueConstraint("refund_reference", name="uq_payment_refunds_refund_reference"),
        UniqueConstraint("idempotency_key", name="uq_payment_refunds_idempotency_key"),
        CheckConstraint("amount > 0", name="ck_payment_refunds_amount_positive"),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_payment_refunds_currency_iso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    refund_reference: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    payment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[RefundStatus] = mapped_column(
        SQLEnum(
            RefundStatus,
            name="refund_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=RefundStatus.PENDING,
        index=True,
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    gateway_response: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

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
    payment: Mapped["Payment"] = relationship("Payment", back_populates="refunds")
    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="refunds")
    customer: Mapped["Customer"] = relationship("Customer", back_populates="refunds")

    def __repr__(self) -> str:
        return (
            f"<PaymentRefund(id={self.id}, ref='{self.refund_reference}', "
            f"payment_id={self.payment_id}, amount={self.amount} {self.currency}, status='{self.status.value}')>"
        )


class WebhookEvent(Base):
    """
    Stores gateway webhook deliveries for signature auditability and idempotent
    deduplication using a strict UNIQUE constraint on event_id.
    """
    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_webhook_events_event_id"),
        CheckConstraint(
            "processing_status IN ('received', 'processed', 'ignored_stale', 'rejected')",
            name="ck_webhook_events_processing_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    gateway: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="mock_gateway",
        server_default=text("'mock_gateway'"),
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    payment_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    transaction_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)

    signature_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    processing_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="processed",
        server_default=text("'processed'"),
        index=True,
    )
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    payment: Mapped[Optional["Payment"]] = relationship("Payment", back_populates="webhook_events")

    def __repr__(self) -> str:
        return (
            f"<WebhookEvent(id={self.id}, event_id='{self.event_id}', "
            f"type='{self.event_type}', status='{self.processing_status}')>"
        )
