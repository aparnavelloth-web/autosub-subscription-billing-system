from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.models.enums import BillingInterval
from app.schemas.common import normalize_iso_currency


def _normalize_plan_code(value: str) -> str:
    cleaned = value.strip().upper()
    if not cleaned or not all(ch.isalnum() or ch in ("_", "-") for ch in cleaned):
        raise ValueError("Plan code must contain only alphanumeric characters, hyphens, or underscores.")
    return cleaned


class PlanBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Display name of the plan")
    code: str = Field(..., min_length=2, max_length=50, description="Unique plan identifier code")
    description: Optional[str] = Field(None, max_length=2000, description="Plan description")
    price: Decimal = Field(
        ...,
        ge=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        description="Non-negative recurring price",
    )
    currency: str = Field(default="INR", min_length=3, max_length=3, description="3-letter ISO currency code")
    billing_interval: BillingInterval = Field(
        default=BillingInterval.MONTHLY,
        description="Billing cadence (monthly, quarterly, yearly)",
    )
    trial_days: int = Field(default=0, ge=0, le=3650, description="Number of free trial days")
    features: Optional[Union[List[str], Dict[str, Any]]] = Field(
        default_factory=list,
        description="Feature entitlements list or mapping",
    )
    is_active: bool = Field(default=True, description="Whether the plan is active")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Plan name must be at least 2 non-whitespace characters.")
        return cleaned

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        return _normalize_plan_code(v)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return normalize_iso_currency(v)


class PlanCreate(PlanBase):
    """Schema for creating a new subscription plan."""
    pass


class PlanUpdate(BaseModel):
    """Schema for updating an existing subscription plan."""
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    code: Optional[str] = Field(None, min_length=2, max_length=50)
    description: Optional[str] = Field(None, max_length=2000)
    price: Optional[Decimal] = Field(None, ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    billing_interval: Optional[BillingInterval] = None
    trial_days: Optional[int] = Field(None, ge=0, le=3650)
    features: Optional[Union[List[str], Dict[str, Any]]] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Plan name must be at least 2 non-whitespace characters.")
        return cleaned

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _normalize_plan_code(v)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return normalize_iso_currency(v)


class PlanResponse(PlanBase):
    """Schema for returning subscription plan details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

