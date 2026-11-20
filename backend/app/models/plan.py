from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Any, TYPE_CHECKING
from sqlalchemy import (
    Integer,
    String,
    Text,
    Numeric,
    Boolean,
    DateTime,
    Enum as SQLEnum,
    UniqueConstraint,
    CheckConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.base import Base
from app.models.enums import BillingInterval

if TYPE_CHECKING:
    from app.models.subscription import Subscription


class Plan(Base):
    """
    Represents a subscription tier/pricing plan in the recurring revenue model.
    Supports soft deletion via `deleted_at` to preserve historical subscription & invoice references.
    """
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_plans_code"),
        CheckConstraint("price >= 0", name="ck_plans_price_non_negative"),
        CheckConstraint("trial_days >= 0", name="ck_plans_trial_days_non_negative"),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_plans_currency_iso"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    billing_interval: Mapped[BillingInterval] = mapped_column(
        SQLEnum(
            BillingInterval,
            name="billing_interval_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=BillingInterval.MONTHLY,
    )
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True, default=list)
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

    # Relationships (no destructive cascade; plans are soft-deleted)
    subscriptions: Mapped[List["Subscription"]] = relationship(
        "Subscription",
        back_populates="plan",
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.is_active = False
        self.deleted_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        return (
            f"<Plan(id={self.id}, code='{self.code}', "
            f"price={self.price} {self.currency}/{self.billing_interval.value})>"
        )
