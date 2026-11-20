import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.enums import InvoiceStatus
from app.schemas.common import PaginatedResponse
from app.schemas.customer import CustomerCreate, CustomerResponse, CustomerUpdate
from app.schemas.invoice import InvoiceResponse
from app.schemas.subscription import SubscriptionResponse
from app.services.customer_service import (
    CustomerInactiveOrDeletedError,
    CustomerNotFoundError,
    CustomerService,
    DuplicateCustomerError,
)
from app.services.invoice_service import InvoiceService

router = APIRouter()


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Customer",
    description="Registers a new billing customer with normalized email, customer code, billing address, and currency.",
)
def create_customer(
    payload: CustomerCreate,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    try:
        customer = CustomerService.create_customer(db, payload)
        return CustomerResponse.model_validate(customer)
    except DuplicateCustomerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "",
    response_model=PaginatedResponse[CustomerResponse],
    status_code=status.HTTP_200_OK,
    summary="List Customers",
    description="Retrieves a paginated list of customers. Soft-deleted customers are excluded by default.",
)
def list_customers(
    page: int = Query(default=1, ge=1, description="1-indexed page number"),
    size: int = Query(default=20, ge=1, le=100, description="Number of customers per page"),
    include_deleted: bool = Query(default=False, description="Include soft-deleted customers"),
    active_only: bool = Query(default=True, description="Filter to active customers only"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[CustomerResponse]:
    items, total = CustomerService.list_customers(
        db=db,
        page=page,
        size=size,
        include_deleted=include_deleted,
        active_only=active_only,
    )
    pages = math.ceil(total / size) if total > 0 else 0
    return PaginatedResponse[CustomerResponse](
        items=[CustomerResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Customer by ID",
)
def get_customer(
    customer_id: int,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    try:
        customer = CustomerService.get_customer(db, customer_id)
        return CustomerResponse.model_validate(customer)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put(
    "/{customer_id}",
    response_model=CustomerResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Customer",
)
def update_customer(
    customer_id: int,
    payload: CustomerUpdate,
    db: Session = Depends(get_db),
) -> CustomerResponse:
    try:
        customer = CustomerService.update_customer(db, customer_id, payload)
        return CustomerResponse.model_validate(customer)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicateCustomerError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except CustomerInactiveOrDeletedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/{customer_id}/subscriptions",
    response_model=PaginatedResponse[SubscriptionResponse],
    status_code=status.HTTP_200_OK,
    summary="List Customer Subscriptions",
    description="Retrieves paginated subscriptions belonging to a specific customer.",
)
def list_customer_subscriptions(
    customer_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse[SubscriptionResponse]:
    try:
        items, total = CustomerService.list_customer_subscriptions(db, customer_id, page=page, size=size)
        pages = math.ceil(total / size) if total > 0 else 0
        return PaginatedResponse[SubscriptionResponse](
            items=[SubscriptionResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            size=size,
            pages=pages,
        )
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{customer_id}/invoices",
    response_model=PaginatedResponse[InvoiceResponse],
    status_code=status.HTTP_200_OK,
    summary="List Customer Invoices",
    description="Retrieves paginated invoices belonging to a specific customer with optional status filtering.",
)
def list_customer_invoices(
    customer_id: int,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    invoice_status: Optional[InvoiceStatus] = Query(
        default=None,
        alias="status",
        description="Filter by invoice status.",
    ),
    db: Session = Depends(get_db),
) -> PaginatedResponse[InvoiceResponse]:
    try:
        items, total = InvoiceService.list_customer_invoices(
            db,
            customer_id=customer_id,
            page=page,
            size=size,
            status=invoice_status,
        )
        pages = math.ceil(total / size) if total > 0 else 0
        return PaginatedResponse[InvoiceResponse](
            items=[InvoiceResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            size=size,
            pages=pages,
        )
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

