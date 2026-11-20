from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.common import normalize_iso_currency, validate_and_normalize_email


def _normalize_customer_code(value: str) -> str:
    cleaned = value.strip().upper()
    if not cleaned or not all(ch.isalnum() or ch in ("_", "-") for ch in cleaned):
        raise ValueError("Customer code must contain only alphanumeric characters, hyphens, or underscores.")
    return cleaned


class CustomerBase(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=150, description="Full contact name")
    email: str = Field(..., max_length=255, description="Unique customer billing email")
    phone: Optional[str] = Field(None, max_length=30, description="Contact phone number")
    company_name: Optional[str] = Field(None, max_length=150, description="Organization / company name")
    billing_address: Optional[str] = Field(None, max_length=1000, description="Full billing address")
    tax_id: Optional[str] = Field(None, max_length=50, description="Tax / GST / VAT identification number")
    currency: str = Field(default="INR", min_length=3, max_length=3, description="3-letter ISO currency code")
    is_active: bool = Field(default=True, description="Whether customer account is active")

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Full name must be at least 2 non-whitespace characters.")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        return validate_and_normalize_email(v)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return normalize_iso_currency(v)


class CustomerCreate(CustomerBase):
    """Schema for registering a new customer."""
    customer_code: Optional[str] = Field(
        None,
        min_length=2,
        max_length=50,
        description="Optional unique customer code (auto-generated if omitted)",
    )

    @field_validator("customer_code")
    @classmethod
    def validate_customer_code(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return _normalize_customer_code(v)


class CustomerUpdate(BaseModel):
    """Schema for updating an existing customer profile."""
    full_name: Optional[str] = Field(None, min_length=2, max_length=150)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=30)
    company_name: Optional[str] = Field(None, max_length=150)
    billing_address: Optional[str] = Field(None, max_length=1000)
    tax_id: Optional[str] = Field(None, max_length=50)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    is_active: Optional[bool] = None

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Full name must be at least 2 non-whitespace characters.")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_and_normalize_email(v)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return normalize_iso_currency(v)


class CustomerResponse(CustomerBase):
    """Schema for returning customer details."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_code: str
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

