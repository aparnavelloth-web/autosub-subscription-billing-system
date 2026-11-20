from datetime import datetime
from decimal import Decimal
import enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.models.enums import InvoiceStatus, PaymentMethod, PaymentStatus, RefundStatus
from app.schemas.common import normalize_iso_currency


class MockPaymentOutcome(str, enum.Enum):
    """Supported deterministic outcomes for the simulated payment gateway."""
    SUCCEED = "succeed"
    FAIL = "fail"
    PENDING = "pending"


class MockWebhookEventType(str, enum.Enum):
    """Supported mock gateway webhook event types."""
    PAYMENT_SUCCEEDED = "payment.succeeded"
    PAYMENT_FAILED = "payment.failed"
    REFUND_SUCCEEDED = "refund.succeeded"
    REFUND_FAILED = "refund.failed"


class PaymentBase(BaseModel):
    invoice_id: int = Field(..., gt=0, description="Target invoice ID")
    customer_id: int = Field(..., gt=0, description="Paying customer ID")
    amount: Decimal = Field(
        ...,
        gt=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        description="Positive payment amount",
    )
    currency: str = Field(default="INR", min_length=3, max_length=3)
    payment_method: PaymentMethod = Field(
        default=PaymentMethod.SIMULATED,
        description="Payment method used for settlement",
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        return normalize_iso_currency(v)


class PaymentCreate(PaymentBase):
    """Schema for creating/recording a payment transaction."""
    transaction_reference: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100,
        description="Unique gateway transaction reference (auto-generated if omitted)",
    )
    idempotency_key: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Optional client idempotency key to prevent duplicate charges",
    )


class PaymentInitiateRequest(BaseModel):
    """
    Request schema for initiating a payment via MockPaymentGateway against an OPEN invoice.
    Optional customer_id, amount, and currency default from the target invoice when omitted,
    and are strictly validated against the invoice when supplied.
    """
    invoice_id: int = Field(..., gt=0, description="Target OPEN invoice ID")
    customer_id: Optional[int] = Field(
        None,
        gt=0,
        description="Paying customer ID (validated against invoice owner if provided)",
    )
    amount: Optional[Decimal] = Field(
        None,
        gt=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        description="Positive payment amount (defaults to remaining invoice balance if omitted)",
    )
    currency: Optional[str] = Field(
        None,
        min_length=3,
        max_length=3,
        description="3-letter ISO currency code (must match invoice currency if provided)",
    )
    payment_method: PaymentMethod = Field(
        default=PaymentMethod.SIMULATED,
        description="Payment instrument used for settlement",
    )
    transaction_reference: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100,
        description="Optional unique transaction reference (auto-generated if omitted)",
    )
    idempotency_key: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Optional client idempotency key for safe retries",
    )
    simulate_outcome: MockPaymentOutcome = Field(
        default=MockPaymentOutcome.SUCCEED,
        description="Mock gateway outcome to simulate: succeed, fail, or pending",
    )
    failure_reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional custom reason when simulating a failed or pending payment",
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return normalize_iso_currency(v)

    @field_validator("idempotency_key", "transaction_reference")
    @classmethod
    def strip_optional_tokens(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier cannot be empty or whitespace only.")
        return cleaned


class PaymentUpdate(BaseModel):
    """Schema for updating payment settlement status and gateway diagnostics."""
    status: Optional[PaymentStatus] = None
    payment_date: Optional[datetime] = None
    failure_reason: Optional[str] = Field(None, max_length=2000)
    gateway_response: Optional[Dict[str, Any]] = None


class PaymentRefundRequest(BaseModel):
    """Request schema for initiating a partial or full refund against a settled payment."""
    amount: Optional[Decimal] = Field(
        None,
        gt=Decimal("0.00"),
        max_digits=12,
        decimal_places=2,
        description="Positive refund amount (defaults to remaining refundable amount if omitted)",
    )
    currency: Optional[str] = Field(
        None,
        min_length=3,
        max_length=3,
        description="3-letter ISO currency code (must match payment currency if supplied)",
    )
    reason: Optional[str] = Field(None, max_length=1000, description="Reason for issuing refund")
    refund_reference: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100,
        description="Optional unique refund reference (auto-generated if omitted)",
    )
    idempotency_key: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Optional client idempotency key to prevent duplicate refunds",
    )
    simulate_outcome: MockPaymentOutcome = Field(
        default=MockPaymentOutcome.SUCCEED,
        description="Mock refund gateway outcome: succeed, fail, or pending",
    )
    failure_reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional failure/pending diagnostic note for mock simulation",
    )

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return normalize_iso_currency(v)

    @field_validator("idempotency_key", "refund_reference")
    @classmethod
    def strip_optional_tokens(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Identifier cannot be empty or whitespace only.")
        return cleaned


class PaymentRefundResponse(BaseModel):
    """Schema for returning individual refund ledger entries."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    refund_reference: str
    idempotency_key: Optional[str] = None
    payment_id: int
    invoice_id: int
    customer_id: int
    amount: Decimal
    currency: str
    status: RefundStatus
    reason: Optional[str] = None
    gateway_response: Optional[Dict[str, Any]] = None
    refunded_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class RefundActionResponse(PaymentRefundResponse):
    """Extended refund response including post-refund payment and invoice accounting summaries."""
    payment_status: PaymentStatus
    payment_refunded_amount: Decimal
    remaining_refundable_amount: Decimal
    invoice_status: InvoiceStatus
    invoice_amount_paid: Decimal
    invoice_net_collected_amount: Decimal


class PaymentResponse(PaymentBase):
    """Schema for returning payment transaction details with refund history."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_reference: str
    idempotency_key: Optional[str] = None
    refunded_amount: Decimal = Decimal("0.00")
    remaining_refundable_amount: Decimal = Decimal("0.00")
    status: PaymentStatus
    payment_date: Optional[datetime] = None
    failure_reason: Optional[str] = None
    gateway_response: Optional[Dict[str, Any]] = None
    refunds: List[PaymentRefundResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MockWebhookPayload(BaseModel):
    """Incoming payload schema for mock payment gateway webhook events."""
    event_id: str = Field(..., min_length=2, max_length=100, description="Unique gateway event identifier")
    event_type: MockWebhookEventType = Field(..., description="Webhook event type")
    transaction_reference: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100,
        description="Target payment transaction reference (required for payment.* events)",
    )
    refund_reference: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100,
        description="Target refund reference (required for refund.* events)",
    )
    amount: Optional[Decimal] = Field(None, gt=Decimal("0.00"), max_digits=12, decimal_places=2)
    currency: Optional[str] = Field(None, min_length=3, max_length=3)
    failure_reason: Optional[str] = Field(None, max_length=1000)
    gateway_data: Optional[Dict[str, Any]] = None

    @field_validator("event_id")
    @classmethod
    def strip_required_event_id(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("event_id must be at least 2 non-whitespace characters.")
        return cleaned

    @field_validator("transaction_reference", "refund_reference")
    @classmethod
    def strip_optional_refs(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Reference identifier must be at least 2 non-whitespace characters.")
        return cleaned

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return normalize_iso_currency(v)

    @model_validator(mode="after")
    def validate_reference_requirements(self) -> "MockWebhookPayload":
        if self.event_type in (MockWebhookEventType.PAYMENT_SUCCEEDED, MockWebhookEventType.PAYMENT_FAILED):
            if not self.transaction_reference:
                raise ValueError(f"transaction_reference is required for '{self.event_type.value}' events.")
        elif self.event_type in (MockWebhookEventType.REFUND_SUCCEEDED, MockWebhookEventType.REFUND_FAILED):
            if not self.refund_reference:
                raise ValueError(f"refund_reference is required for '{self.event_type.value}' events.")
        return self


class WebhookProcessResponse(BaseModel):
    """Response returned after idempotent processing of a verified gateway webhook."""
    event_id: str
    event_type: str
    processing_status: str
    duplicate: bool
    payment_id: Optional[int] = None
    payment_status: Optional[PaymentStatus] = None
    refund_id: Optional[int] = None
    refund_status: Optional[RefundStatus] = None
    invoice_id: Optional[int] = None
    invoice_status: Optional[InvoiceStatus] = None
    message: str

