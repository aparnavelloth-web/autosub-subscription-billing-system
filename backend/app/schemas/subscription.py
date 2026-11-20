from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.enums import BillingInterval, SubscriptionStatus


class SubscriptionBase(BaseModel):
    customer_id: int = Field(..., gt=0, description="ID of the subscribing customer")
    plan_id: int = Field(..., gt=0, description="ID of the selected subscription plan")
    quantity: int = Field(default=1, gt=0, le=10000, description="Subscription seat/unit quantity")
    cancel_at_period_end: bool = Field(
        default=False,
        description="Whether the subscription cancels automatically at the end of the current period",
    )


class SubscriptionCreate(SubscriptionBase):
    """Schema for creating a new recurring subscription."""
    subscription_code: Optional[str] = Field(
        None,
        min_length=2,
        max_length=50,
        description="Optional unique subscription code (auto-generated if omitted)",
    )
    status: Optional[SubscriptionStatus] = Field(
        None,
        description="Initial status (defaults to trial if plan has trial_days > 0, else active)",
    )
    start_date: Optional[datetime] = Field(None, description="Subscription start timestamp")
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None

    @field_validator("subscription_code")
    @classmethod
    def validate_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip().upper()
        if not cleaned or not all(ch.isalnum() or ch in ("_", "-") for ch in cleaned):
            raise ValueError("Subscription code must contain only alphanumeric characters, hyphens, or underscores.")
        return cleaned

    @model_validator(mode="after")
    def validate_dates(self) -> "SubscriptionCreate":
        if self.current_period_start and self.current_period_end:
            if self.current_period_end <= self.current_period_start:
                raise ValueError("current_period_end must be strictly greater than current_period_start.")
        if self.trial_start and self.trial_end:
            if self.trial_end <= self.trial_start:
                raise ValueError("trial_end must be strictly greater than trial_start.")
        return self


class SubscriptionUpdate(BaseModel):
    """Schema for updating, upgrading, or cancelling an existing subscription."""
    plan_id: Optional[int] = Field(None, gt=0, description="New plan ID when upgrading or downgrading")
    status: Optional[SubscriptionStatus] = None
    quantity: Optional[int] = Field(None, gt=0, le=10000)
    cancel_at_period_end: Optional[bool] = None


class SubscriptionPlanChangeRequest(BaseModel):
    """Schema for upgrading or downgrading a subscription to a different active plan."""
    new_plan_id: int = Field(..., gt=0, description="ID of the target active plan")
    quantity: Optional[int] = Field(None, gt=0, le=10000, description="Optional updated seat/unit quantity")
    reset_billing_period: bool = Field(
        default=False,
        description="If true, resets period start/end from current time using the new plan interval",
    )
    as_of: Optional[datetime] = Field(
        None,
        description="Optional evaluation timestamp for proration calculation (defaults to current UTC time)",
    )


class ProrationPreviewRequest(BaseModel):
    """Schema for previewing plan change proration calculations without modifying state."""
    new_plan_id: int = Field(..., gt=0, description="Target plan ID for proration preview")
    quantity: Optional[int] = Field(
        None,
        gt=0,
        le=10000,
        description="Optional target unit quantity (defaults to current subscription quantity)",
    )
    as_of: Optional[datetime] = Field(
        None,
        description="Optional evaluation timestamp for proration calculation (defaults to current UTC time)",
    )


class ProrationPreviewResponse(BaseModel):
    """
    Detailed proration preview response breaking down remaining period days,
    unused credit, prorated new charge, and net amount due.
    """
    subscription_id: int
    current_plan_id: int
    current_plan_name: str
    current_plan_code: str
    current_price: Decimal
    current_quantity: int
    new_plan_id: int
    new_plan_name: str
    new_plan_code: str
    new_price: Decimal
    new_quantity: int
    currency: str
    billing_interval: BillingInterval

    # Proration Core Dimensions
    total_billing_days: int = Field(..., description="Total days in the current billing period")
    remaining_billing_days: int = Field(..., description="Remaining unused days in the current billing period")
    unused_credit: Decimal = Field(..., description="Unused credit from current plan for remaining days")
    prorated_charge: Decimal = Field(..., description="Prorated charge for the new plan for remaining days")
    net_amount_due: Decimal = Field(
        ...,
        description="Net amount due (prorated_charge - unused_credit). Positive for upgrade due; negative for credit.",
    )
    change_type: str = Field(..., description="'upgrade', 'downgrade', or 'switch'")
    tax_amount: Decimal = Field(default=Decimal("0.00"), description="Estimated tax calculated on net positive charge")
    total_due: Decimal = Field(..., description="Final amount payable immediately: max(0.00, net_amount_due + tax_amount)")
    credit_carried_forward: Decimal = Field(
        default=Decimal("0.00"),
        description="Credit balance remaining when unused credit exceeds new plan charge",
    )

    current_period_start: datetime
    current_period_end: datetime
    evaluated_at: datetime


class SubscriptionCancelRequest(BaseModel):
    """Schema for immediate or end-of-billing-cycle subscription cancellation."""
    immediate: bool = Field(
        default=True,
        description="If true, cancels immediately; if false, sets cancel_at_period_end=True",
    )
    reason: Optional[str] = Field(None, max_length=500, description="Optional cancellation reason for audit logging")


class SubscriptionPauseRequest(BaseModel):
    """Schema for pausing an active paid subscription."""
    reason: Optional[str] = Field(None, max_length=255, description="Optional pause reason for audit logging")


class SubscriptionResumeRequest(BaseModel):
    """Schema for resuming a currently paused subscription."""
    reason: Optional[str] = Field(None, max_length=255, description="Optional resume note for audit logging")


class SubscriptionResponse(BaseModel):
    """Schema for returning subscription details including snapshotted historical pricing."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    subscription_code: str
    customer_id: int
    plan_id: int
    status: SubscriptionStatus
    quantity: int

    # Historical price snapshot fields
    unit_price: Decimal
    currency: str
    billing_interval: BillingInterval

    start_date: datetime
    current_period_start: datetime
    current_period_end: datetime
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    cancel_at_period_end: bool
    cancelled_at: Optional[datetime] = None

    # Orthogonal pause state
    is_paused: bool = False
    paused_at: Optional[datetime] = None
    resumed_at: Optional[datetime] = None
    paused_remaining_seconds: Optional[int] = None
    pause_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class SubscriptionPlanChangeResponse(SubscriptionResponse):
    """
    Extended response returned by change-plan containing both the updated
    subscription state and the generated proration adjustment invoice reference.
    """
    invoice_id: Optional[int] = Field(None, description="ID of the generated proration adjustment invoice")
    invoice_number: Optional[str] = Field(None, description="Unique number of the generated adjustment invoice")
    proration_net_amount: Optional[Decimal] = Field(None, description="Net prorated amount calculated for this change")


class SubscriptionHistoryEvent(BaseModel):
    """
    Represents an event on the subscription history timeline (e.g., plan upgrade/downgrade,
    invoice generation, status transition, pause, resume).
    """
    id: str
    event_type: str = Field(..., description="'plan_changed', 'invoice_generated', 'status_changed', 'paused', 'resumed'")
    timestamp: datetime
    title: str
    description: str
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    invoice_id: Optional[int] = None
    invoice_number: Optional[str] = None
    details: Optional[dict] = None


