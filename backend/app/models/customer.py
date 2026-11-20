from datetime import datetime, timezone
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    UniqueConstraint,
    CheckConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base

if TYPE_CHECKING:
    from app.models.subscription import Subscription
    from app.models.invoice import Invoice
    from app.models.payment import Payment, PaymentRefund


class Customer(Base):
    """
    Represents a customer / subscriber organization or individual.
    Supports soft deletion via `deleted_at` while strictly protecting financial history via RESTRICT FKs.
    """
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("email", name="uq_customers_email"),
        UniqueConstraint("customer_code", name="uq_customers_customer_code"),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_customers_currency_iso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    customer_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    billing_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tax_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

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

    # Relationships (no cascade delete-orphan; financial history is protected by RESTRICT FKs)
    subscriptions: Mapped[List["Subscription"]] = relationship(
        "Subscription",
        back_populates="customer",
        passive_deletes=True,
    )
    invoices: Mapped[List["Invoice"]] = relationship(
        "Invoice",
        back_populates="customer",
        passive_deletes=True,
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="customer",
        passive_deletes=True,
    )
    refunds: Mapped[List["PaymentRefund"]] = relationship(
        "PaymentRefund",
        back_populates="customer",
        passive_deletes=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.is_active = False
        self.deleted_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return f"<Customer(id={self.id}, code='{self.customer_code}', email='{self.email}')>"
