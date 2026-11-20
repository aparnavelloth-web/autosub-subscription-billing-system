import math
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.enums import InvoiceStatus, PaymentStatus
from app.schemas.common import PaginatedResponse
from app.schemas.invoice import InvoiceGenerateRequest, InvoiceResponse
from app.schemas.payment import PaymentResponse
from app.services.invoice_service import (
    BillingCycleNotFoundError,
    InvalidInvoiceOperationError,
    InvoiceNotFoundError,
    InvoiceService,
)
from app.services.payment_service import PaymentService
from app.services.pdf_service import InvoicePDFService

router = APIRouter()


@router.post(
    "/generate",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate Invoice for Billing Cycle",
    description="Manually generates an itemized invoice for an existing billing cycle with duplicate prevention and concurrency safety.",
)
def generate_invoice(
    payload: InvoiceGenerateRequest,
    db: Session = Depends(get_db),
) -> InvoiceResponse:
    try:
        invoice, created = InvoiceService.generate_invoice_for_billing_cycle(
            db=db,
            billing_cycle_id=payload.billing_cycle_id,
            tax_rate=payload.tax_rate,
            extra_discount=payload.extra_discount,
            due_days=payload.due_days,
            notes=payload.notes,
            actor="api_manual_generation",
        )
        if not created:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An invoice already exists for billing_cycle_id {payload.billing_cycle_id} ('{invoice.invoice_number}').",
            )
        enriched = InvoiceService.get_invoice(db, invoice.id)
        return InvoiceResponse.model_validate(enriched)
    except BillingCycleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidInvoiceOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "",
    response_model=PaginatedResponse[InvoiceResponse],
    status_code=status.HTTP_200_OK,
    summary="List Invoices",
    description="Retrieves a paginated list of invoices with optional customer, subscription, billing cycle, status, search, and date range filters.",
)
def list_invoices(
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    size: int = Query(default=20, ge=1, le=100, description="Number of items per page."),
    customer_id: Optional[int] = Query(default=None, ge=1, description="Filter by customer ID."),
    subscription_id: Optional[int] = Query(default=None, ge=1, description="Filter by subscription ID."),
    billing_cycle_id: Optional[int] = Query(default=None, ge=1, description="Filter by billing cycle ID."),
    invoice_status: Optional[InvoiceStatus] = Query(
        default=None,
        alias="status",
        description="Filter by invoice status.",
    ),
    query: Optional[str] = Query(
        default=None,
        alias="query",
        description="Search by partial invoice number (case-insensitive).",
    ),
    invoice_number: Optional[str] = Query(
        default=None,
        description="Filter or search by partial invoice number (case-insensitive).",
    ),
    search: Optional[str] = Query(
        default=None,
        description="Filter or search by partial invoice number (case-insensitive).",
    ),
    date_from: Optional[datetime] = Query(
        default=None,
        description="Filter invoices issued on or after this ISO datetime.",
    ),
    date_to: Optional[datetime] = Query(
        default=None,
        description="Filter invoices issued on or before this ISO datetime.",
    ),
    db: Session = Depends(get_db),
) -> PaginatedResponse[InvoiceResponse]:
    search_term = query or invoice_number or search
    items, total = InvoiceService.list_invoices(
        db=db,
        page=page,
        size=size,
        customer_id=customer_id,
        subscription_id=subscription_id,
        billing_cycle_id=billing_cycle_id,
        status=invoice_status,
        invoice_number=search_term,
        date_from=date_from,
        date_to=date_to,
    )
    pages = math.ceil(total / size) if total > 0 else 0
    return PaginatedResponse[InvoiceResponse](
        items=[InvoiceResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/{invoice_id}",
    response_model=InvoiceResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Invoice",
    description="Retrieves full invoice details and itemized line items by invoice ID.",
)
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
) -> InvoiceResponse:
    try:
        invoice = InvoiceService.get_invoice(db, invoice_id)
        return InvoiceResponse.model_validate(invoice)
    except InvoiceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{invoice_id}/pdf",
    status_code=status.HTTP_200_OK,
    summary="Download Invoice PDF",
    description="Generates and returns an itemized, downloadable PDF statement for the specified invoice.",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Returns the generated PDF file stream.",
        },
        404: {"description": "Invoice not found"},
    },
)
def download_invoice_pdf(
    invoice_id: int,
    db: Session = Depends(get_db),
) -> Response:
    try:
        invoice = InvoiceService.get_invoice(db, invoice_id)
        pdf_bytes = InvoicePDFService.generate_invoice_pdf(invoice)
        filename = f"invoice-{invoice.invoice_number}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": "application/pdf",
            },
        )
    except InvoiceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{invoice_id}/payments",
    response_model=PaginatedResponse[PaymentResponse],
    status_code=status.HTTP_200_OK,
    summary="List Invoice Payments",
    description="Retrieves paginated payment attempts (failed, pending, succeeded, refunded) for a specific invoice.",
)
def list_invoice_payments(
    invoice_id: int,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)."),
    size: int = Query(default=20, ge=1, le=100, description="Number of items per page."),
    payment_status: Optional[PaymentStatus] = Query(
        default=None,
        alias="status",
        description="Filter by payment status.",
    ),
    db: Session = Depends(get_db),
) -> PaginatedResponse[PaymentResponse]:
    try:
        items, total = PaymentService.list_invoice_payments(
            db=db,
            invoice_id=invoice_id,
            page=page,
            size=size,
            status=payment_status,
        )
        pages = math.ceil(total / size) if total > 0 else 0
        return PaginatedResponse[PaymentResponse](
            items=[PaymentResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            size=size,
            pages=pages,
        )
    except InvoiceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

