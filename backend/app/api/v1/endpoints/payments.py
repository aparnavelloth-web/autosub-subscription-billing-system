import math
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.enums import PaymentMethod, PaymentStatus
from app.schemas.common import PaginatedResponse
from app.schemas.payment import (
    PaymentInitiateRequest,
    PaymentRefundRequest,
    PaymentResponse,
    RefundActionResponse,
)
from app.services.invoice_service import InvoiceNotFoundError
from app.services.payment_service import (
    DuplicatePaymentError,
    InvalidPaymentOperationError,
    PaymentNotFoundError,
    PaymentService,
)

router = APIRouter()


@router.post(
    "",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Process Invoice Payment",
    description=(
        "Initiates a simulated payment via MockPaymentGateway for an OPEN invoice. "
        "Supports partial payments, idempotency keys, and succeeded/failed/pending outcomes."
    ),
)
def create_payment(
    payload: PaymentInitiateRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> PaymentResponse:
    client_ip = request.client.host if request.client else None
    try:
        payment, created = PaymentService.process_payment(
            db=db,
            payload=payload,
            actor="api",
            ip_address=client_ip,
        )
        if not created:
            response.status_code = status.HTTP_200_OK
        return PaymentResponse.model_validate(payment)
    except InvoiceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicatePaymentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPaymentOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "",
    response_model=PaginatedResponse[PaymentResponse],
    status_code=status.HTTP_200_OK,
    summary="List Payments",
    description="Retrieves a paginated list of payment transactions with optional customer, invoice, status, and method filters.",
)
def list_payments(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    size: int = Query(default=20, ge=1, le=100, description="Number of items per page."),
    customer_id: Optional[int] = Query(default=None, ge=1, description="Filter by customer ID."),
    invoice_id: Optional[int] = Query(default=None, ge=1, description="Filter by invoice ID."),
    payment_status: Optional[PaymentStatus] = Query(
        default=None,
        alias="status",
        description="Filter by payment status.",
    ),
    payment_method: Optional[PaymentMethod] = Query(
        default=None,
        description="Filter by payment method.",
    ),
    db: Session = Depends(get_db),
) -> PaginatedResponse[PaymentResponse]:
    items, total = PaymentService.list_payments(
        db=db,
        page=page,
        size=size,
        customer_id=customer_id,
        invoice_id=invoice_id,
        status=payment_status,
        payment_method=payment_method,
    )
    pages = math.ceil(total / size) if total > 0 else 0
    return PaginatedResponse[PaymentResponse](
        items=[PaymentResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Payment",
    description="Retrieves payment details by payment ID.",
)
def get_payment(
    payment_id: int,
    db: Session = Depends(get_db),
) -> PaymentResponse:
    try:
        payment = PaymentService.get_payment(db, payment_id)
        return PaymentResponse.model_validate(payment)
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{payment_id}/refund",
    response_model=RefundActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Refund Settled Payment",
    description=(
        "Issues a partial or full simulated refund against a SUCCEEDED payment via MockPaymentGateway. "
        "Retains Payment.status='succeeded' on partial refunds and preserves historical Invoice.amount_paid."
    ),
)
def refund_payment(
    payment_id: int,
    request: Request,
    response: Response,
    payload: PaymentRefundRequest = Body(default_factory=PaymentRefundRequest),
    db: Session = Depends(get_db),
) -> RefundActionResponse:
    client_ip = request.client.host if request.client else None
    try:
        refund, created = PaymentService.refund_payment(
            db=db,
            payment_id=payment_id,
            payload=payload,
            actor="api",
            ip_address=client_ip,
        )
        if not created:
            response.status_code = status.HTTP_200_OK

        payment = refund.payment
        invoice = refund.invoice
        return RefundActionResponse(
            id=refund.id,
            refund_reference=refund.refund_reference,
            idempotency_key=refund.idempotency_key,
            payment_id=refund.payment_id,
            invoice_id=refund.invoice_id,
            customer_id=refund.customer_id,
            amount=refund.amount,
            currency=refund.currency,
            status=refund.status,
            reason=refund.reason,
            gateway_response=refund.gateway_response,
            refunded_at=refund.refunded_at,
            created_at=refund.created_at,
            updated_at=refund.updated_at,
            payment_status=payment.status,
            payment_refunded_amount=payment.refunded_amount,
            remaining_refundable_amount=payment.remaining_refundable_amount,
            invoice_status=invoice.status,
            invoice_amount_paid=invoice.amount_paid,
            invoice_net_collected_amount=invoice.net_collected_amount,
        )
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicatePaymentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidPaymentOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

