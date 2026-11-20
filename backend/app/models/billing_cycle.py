from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING
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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base
from app.models.enums import BillingCycleStatus

if TYPE_CHECKING:
    from app.models.subscription import Subscription
    from app.models.invoice import Invoice


class BillingCycle(Base):
    """
    Represents a distinct recurring billing window for a Subscription.
    Ensures idempotent automated billing runs and tracks cycle execution state.
    """
    __tablename__ = "billing_cycles"
    __table_args__ = (
        UniqueConstraint("subscription_id", "cycle_number", name="uq_billing_cycles_subscription_cycle"),
        CheckConstraint("cycle_number > 0", name="ck_billing_cycles_cycle_number_positive"),
        CheckConstraint("amount >= 0", name="ck_billing_cycles_amount_non_negative"),
        CheckConstraint("period_end > period_start", name="ck_billing_cycles_period_valid"),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_billing_cycles_currency_iso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("subscriptions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    status: Mapped[BillingCycleStatus] = mapped_column(
        SQLEnum(
            BillingCycleStatus,
            name="billing_cycle_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=BillingCycleStatus.PENDING,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    subscription: Mapped["Subscription"] = relationship("Subscription", back_populates="billing_cycles")
    invoice: Mapped[Optional["Invoice"]] = relationship(
        "Invoice",
        back_populates="billing_cycle",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<BillingCycle(id={self.id}, sub_id={self.subscription_id}, "
            f"cycle={self.cycle_number}, status='{self.status.value}')>"
        )
