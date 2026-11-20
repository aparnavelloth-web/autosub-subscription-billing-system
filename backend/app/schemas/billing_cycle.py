from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.enums import BillingCycleStatus
from app.schemas.common import normalize_iso_currency


class BillingCycleBase(BaseModel):
    subscription_id: int = Field(..., gt=0, description="Target subscription ID")
    cycle_number: int = Field(..., gt=0, description="Sequential billing cycle index (1, 2, ...)")
    period_start: datetime
    period_end: datetime
    billing_date: datetime
    amount: Decimal = Field(
        ...,
        ge=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        description="Calculated recurring amount for this cycle",
    )
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return normalize_iso_currency(v)

    @model_validator(mode="after")
    def validate_period(self) -> "BillingCycleBase":
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be strictly greater than period_start.")
        return self


class BillingCycleCreate(BillingCycleBase):
    """Schema for scheduling or creating a new billing cycle record."""
    status: BillingCycleStatus = Field(default=BillingCycleStatus.PENDING)


class BillingCycleUpdate(BaseModel):
    """Schema for updating billing cycle execution status."""
    status: Optional[BillingCycleStatus] = None
    processed_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(None, max_length=2000)


class BillingCycleResponse(BillingCycleBase):
    """Schema for returning billing cycle execution details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: BillingCycleStatus
    processed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

