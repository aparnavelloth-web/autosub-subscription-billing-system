from datetime import datetime
from decimal import Decimal
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    Integer,
    String,
    Numeric,
    Boolean,
    DateTime,
    ForeignKey,
    Enum as SQLEnum,
    UniqueConstraint,
    CheckConstraint,
    Index,
    func,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, Session
from app.database.base import Base
from app.models.enums import SubscriptionStatus, BillingInterval

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.plan import Plan
    from app.models.billing_cycle import BillingCycle
    from app.models.invoice import Invoice


class Subscription(Base):
    """
    Represents an active, trialing, past_due, or cancelled recurring subscription
    binding a Customer to a Plan, with snapshotted historical pricing fields.
    """
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("subscription_code", name="uq_subscriptions_subscription_code"),
        CheckConstraint("quantity > 0", name="ck_subscriptions_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_subscriptions_unit_price_non_negative"),
        CheckConstraint("current_period_end > current_period_start", name="ck_subscriptions_period_valid"),
        CheckConstraint(
            "trial_end IS NULL OR trial_start IS NULL OR trial_end > trial_start",
            name="ck_subscriptions_trial_valid",
        ),
        CheckConstraint("char_length(currency) = 3 AND currency = UPPER(currency)", name="ck_subscriptions_currency_iso"),
        CheckConstraint(
            "paused_remaining_seconds IS NULL OR paused_remaining_seconds >= 0",
            name="ck_subscriptions_paused_remaining_non_negative",
        ),
        Index("ix_subscriptions_status_period_end", "status", "current_period_end"),
        Index("ix_subscriptions_billable_lookup", "status", "is_paused", "current_period_end"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, index=True)
    subscription_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        SQLEnum(
            SubscriptionStatus,
            name="subscription_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=SubscriptionStatus.ACTIVE,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Historical pricing snapshot captured from Plan upon creation or upgrade
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    billing_interval: Mapped[BillingInterval] = mapped_column(
        SQLEnum(
            BillingInterval,
            name="billing_interval_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )

    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    trial_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Orthogonal Pause/Resume State (preserves SubscriptionStatus enum compatibility)
    is_paused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_remaining_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pause_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

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

    # Relationships (no delete-orphan cascade to protect historical billing cycles & invoices)
    customer: Mapped["Customer"] = relationship("Customer", back_populates="subscriptions")
    plan: Mapped["Plan"] = relationship("Plan", back_populates="subscriptions")
    billing_cycles: Mapped[List["BillingCycle"]] = relationship(
        "BillingCycle",
        back_populates="subscription",
        passive_deletes=True,
    )
    invoices: Mapped[List["Invoice"]] = relationship(
        "Invoice",
        back_populates="subscription",
        passive_deletes=True,
    )

    @property
    def is_billable(self) -> bool:
        """
        Only active, non-paused subscriptions are eligible for recurring billing runs.
        """
        return self.status == SubscriptionStatus.ACTIVE and not self.is_paused

    def apply_plan_snapshot(self, plan: "Plan", update_plan_link: bool = True) -> None:
        """
        Captures contracted pricing snapshot from the specified Plan.
        Call when initializing or upgrading/changing a subscription's plan.
        """
        if update_plan_link:
            self.plan = plan
            if plan.id is not None:
                self.plan_id = plan.id
        self.unit_price = plan.price
        self.currency = plan.currency
        self.billing_interval = plan.billing_interval

    def __repr__(self) -> str:
        return (
            f"<Subscription(id={self.id}, code='{self.subscription_code}', "
            f"status='{self.status.value}', unit_price={self.unit_price})>"
        )


@event.listens_for(Session, "before_flush")
def _populate_subscription_pricing_snapshot(session: Session, flush_context, instances) -> None:
    """
    Ensures newly created Subscriptions automatically capture unit_price, currency,
    and billing_interval from the selected Plan if not explicitly set.
    """
    from app.models.plan import Plan

    for obj in session.new:
        if not isinstance(obj, Subscription):
            continue
        if obj.unit_price is None or obj.currency is None or obj.billing_interval is None:
            plan = obj.plan
            if plan is None and obj.plan_id is not None:
                plan = session.get(Plan, obj.plan_id)
            if plan is not None:
                if obj.unit_price is None:
                    obj.unit_price = plan.price
                if obj.currency is None:
                    obj.currency = plan.currency
                if obj.billing_interval is None:
                    obj.billing_interval = plan.billing_interval
