import enum


class BillingInterval(str, enum.Enum):
    """
    Supported recurring billing cadences for subscription plans.
    """
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class SubscriptionStatus(str, enum.Enum):
    """
    Subscription lifecycle states per project specification.
    """
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


class BillingCycleStatus(str, enum.Enum):
    """
    Execution status of an individual recurring billing cycle window.
    """
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class InvoiceStatus(str, enum.Enum):
    """
    State of an invoice within the billing lifecycle.
    """
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class InvoiceItemType(str, enum.Enum):
    """
    Classification of individual invoice line items.
    """
    BASE_SUBSCRIPTION = "base_subscription"
    PLAN_UPGRADE = "plan_upgrade"
    PRORATION_CREDIT = "proration_credit"
    PRORATION_DEBIT = "proration_debit"
    USAGE = "usage"
    TAX = "tax"


class PaymentStatus(str, enum.Enum):
    """
    Settlement status of a payment transaction attempt.
    """
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentMethod(str, enum.Enum):
    """
    Payment instruments supported for settling invoices.
    """
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    BANK_TRANSFER = "bank_transfer"
    UPI = "upi"
    SIMULATED = "simulated"


class RefundStatus(str, enum.Enum):
    """
    Settlement status of a refund transaction against a Payment.
    """
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
