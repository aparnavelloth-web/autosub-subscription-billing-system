"""
Business Services Package
Contains core business logic, recurring billing workflows, and payment gateway abstractions.
"""
from app.services.plan_service import (
    PlanService,
    PlanNotFoundError,
    DuplicatePlanCodeError,
    PlanArchivedError,
)
from app.services.customer_service import (
    CustomerService,
    CustomerNotFoundError,
    DuplicateCustomerError,
    CustomerInactiveOrDeletedError,
)
from app.services.subscription_service import (
    SubscriptionService,
    SubscriptionNotFoundError,
    DuplicateSubscriptionCodeError,
    InvalidSubscriptionStateError,
    SubscriptionPauseConflictError,
    ALLOWED_SUBSCRIPTION_TRANSITIONS,
)
from app.services.billing_dates import (
    add_calendar_months,
    calculate_period_end,
    ensure_utc,
)
from app.services.billing_cycle_service import BillingCycleService
from app.services.invoice_service import (
    BillingCycleNotFoundError,
    InvalidInvoiceOperationError,
    InvoiceNotFoundError,
    InvoiceService,
)
from app.services.mock_payment_gateway import (
    InvalidWebhookSignatureError,
    MockChargeResult,
    MockPaymentGateway,
    MockPaymentOutcome,
    MockRefundResult,
)
from app.services.payment_service import (
    DuplicatePaymentError,
    InvalidPaymentOperationError,
    PaymentNotFoundError,
    PaymentService,
    WebhookConflictError,
    WebhookService,
)
from app.services.auth_service import (
    AuthService,
    DuplicateUserError,
    InvalidCredentialsError,
    InactiveUserError,
)

__all__ = [
    "PlanService",
    "PlanNotFoundError",
    "DuplicatePlanCodeError",
    "PlanArchivedError",
    "CustomerService",
    "CustomerNotFoundError",
    "DuplicateCustomerError",
    "CustomerInactiveOrDeletedError",
    "SubscriptionService",
    "SubscriptionNotFoundError",
    "DuplicateSubscriptionCodeError",
    "InvalidSubscriptionStateError",
    "SubscriptionPauseConflictError",
    "ALLOWED_SUBSCRIPTION_TRANSITIONS",
    "add_calendar_months",
    "calculate_period_end",
    "ensure_utc",
    "BillingCycleService",
    "InvoiceService",
    "InvoiceNotFoundError",
    "BillingCycleNotFoundError",
    "InvalidInvoiceOperationError",
    "MockChargeResult",
    "MockRefundResult",
    "MockPaymentGateway",
    "MockPaymentOutcome",
    "InvalidWebhookSignatureError",
    "PaymentService",
    "WebhookService",
    "PaymentNotFoundError",
    "InvalidPaymentOperationError",
    "DuplicatePaymentError",
    "WebhookConflictError",
    "AuthService",
    "DuplicateUserError",
    "InvalidCredentialsError",
    "InactiveUserError",
]
