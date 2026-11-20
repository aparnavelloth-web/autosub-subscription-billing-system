from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.enums import InvoiceItemType, InvoiceStatus
from app.schemas.common import normalize_iso_currency


class InvoiceItemBase(BaseModel):
    item_type: InvoiceItemType = Field(
        default=InvoiceItemType.BASE_SUBSCRIPTION,
        description="Line item category (base_subscription, plan_upgrade, proration_credit, proration_debit, usage, tax)",
    )
    description: str = Field(..., min_length=1, max_length=255)
    quantity: int = Field(default=1, gt=0)
    unit_price: Decimal = Field(..., ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    amount: Decimal = Field(..., ge=Decimal("0.00"), max_digits=12, decimal_places=2)


class InvoiceItemCreate(InvoiceItemBase):
    """Schema for creating an invoice line item."""
    pass


class InvoiceItemResponse(InvoiceItemBase):
    """Schema for returning an invoice line item."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    created_at: datetime


class InvoiceBase(BaseModel):
    customer_id: int = Field(..., gt=0)
    subscription_id: Optional[int] = Field(None, gt=0)
    billing_cycle_id: Optional[int] = Field(None, gt=0)
    subtotal: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    tax_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    discount_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    total_amount: Decimal = Field(..., ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    issue_date: Optional[datetime] = None
    due_date: datetime
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return normalize_iso_currency(v)

    @model_validator(mode="after")
    def validate_dates(self) -> "InvoiceBase":
        if self.issue_date and self.due_date < self.issue_date:
            raise ValueError("due_date must be greater than or equal to issue_date.")
        return self


class InvoiceCreate(InvoiceBase):
    """Schema for creating a formal invoice with optional itemized charges."""
    invoice_number: Optional[str] = Field(None, min_length=2, max_length=50)
    status: InvoiceStatus = Field(default=InvoiceStatus.OPEN)
    items: List[InvoiceItemCreate] = Field(default_factory=list)


class InvoiceUpdate(BaseModel):
    """Schema for updating an invoice status, payment amount, or PDF metadata."""
    status: Optional[InvoiceStatus] = None
    amount_paid: Optional[Decimal] = Field(None, ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    pdf_path: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=2000)


class InvoiceGenerateRequest(BaseModel):
    """Schema for manually triggering invoice generation for an existing billing cycle."""
    billing_cycle_id: int = Field(..., gt=0, description="Target billing cycle ID.")
    tax_rate: Optional[Decimal] = Field(
        default=None,
        ge=Decimal("0.00"),
        le=Decimal("1.00"),
        description="Optional override tax rate (0.00 to 1.00, e.g. 0.10 for 10%).",
    )
    extra_discount: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        description="Additional one-time discount amount applied to the invoice.",
    )
    due_days: Optional[int] = Field(
        default=None,
        ge=1,
        le=90,
        description="Due date offset in days from the issue date (1-90).",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Optional human-readable notes or terms for the invoice.",
    )


class InvoiceResponse(InvoiceBase):
    """Schema for returning full invoice details and itemized charges."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_number: str
    amount_paid: Decimal
    status: InvoiceStatus
    issue_date: datetime
    paid_at: Optional[datetime] = None
    pdf_path: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    plan_name: Optional[str] = None
    plan_code: Optional[str] = None
    items: List[InvoiceItemResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

