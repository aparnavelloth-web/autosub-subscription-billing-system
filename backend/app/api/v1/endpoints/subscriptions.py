import math
from typing import List, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.enums import SubscriptionStatus
from app.schemas.common import PaginatedResponse
from app.schemas.subscription import (
    ProrationPreviewRequest,
    ProrationPreviewResponse,
    SubscriptionCancelRequest,
    SubscriptionCreate,
    SubscriptionHistoryEvent,
    SubscriptionPauseRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionPlanChangeResponse,
    SubscriptionResponse,
    SubscriptionResumeRequest,
)
from app.services.customer_service import CustomerInactiveOrDeletedError, CustomerNotFoundError
from app.services.plan_service import PlanArchivedError, PlanNotFoundError
from app.services.subscription_service import (
    DuplicateSubscriptionCodeError,
    InvalidSubscriptionStateError,
    SubscriptionNotFoundError,
    SubscriptionPauseConflictError,
    SubscriptionService,
)

router = APIRouter()


@router.post(
    "",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Subscription",
    description=(
        "Creates a recurring subscription for an existing active customer and non-archived plan, "
        "automatically applying trial periods and capturing historical pricing snapshots."
    ),
)
def create_subscription(
    payload: SubscriptionCreate,
    db: Session = Depends(get_db),
) -> SubscriptionResponse:
    try:
        sub = SubscriptionService.create_subscription(db, payload)
        return SubscriptionResponse.model_validate(sub)
    except (CustomerNotFoundError, PlanNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (CustomerInactiveOrDeletedError, PlanArchivedError, InvalidSubscriptionStateError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except DuplicateSubscriptionCodeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "",
    response_model=PaginatedResponse[SubscriptionResponse],
    status_code=status.HTTP_200_OK,
    summary="List Subscriptions",
)
def list_subscriptions(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    customer_id: Optional[int] = Query(default=None, gt=0),
    plan_id: Optional[int] = Query(default=None, gt=0),
    status_filter: Optional[SubscriptionStatus] = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[SubscriptionResponse]:
    items, total = SubscriptionService.list_subscriptions(
        db=db,
        page=page,
        size=size,
        customer_id=customer_id,
        plan_id=plan_id,
        status=status_filter,
    )
    pages = math.ceil(total / size) if total > 0 else 0
    return PaginatedResponse[SubscriptionResponse](
        items=[SubscriptionResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/{subscription_id}",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Subscription by ID",
)
def get_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
) -> SubscriptionResponse:
    try:
        sub = SubscriptionService.get_subscription(db, subscription_id)
        return SubscriptionResponse.model_validate(sub)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{subscription_id}/proration-preview",
    response_model=ProrationPreviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview Subscription Plan Change Proration",
    description=(
        "Calculates and returns a preview of unused credit, new plan charge, and net amount due "
        "for upgrading or downgrading a subscription without modifying state or creating financial records."
    ),
)
def preview_subscription_proration(
    subscription_id: int,
    payload: ProrationPreviewRequest,
    db: Session = Depends(get_db),
) -> ProrationPreviewResponse:
    try:
        return SubscriptionService.preview_proration(db, subscription_id, payload)
    except (SubscriptionNotFoundError, PlanNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (PlanArchivedError, InvalidSubscriptionStateError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{subscription_id}/change-plan",
    response_model=SubscriptionPlanChangeResponse,
    status_code=status.HTTP_200_OK,
    summary="Change Subscription Plan (Upgrade / Downgrade)",
    description=(
        "Upgrades or downgrades an active/trial subscription to another active plan, "
        "calculates proration, creates an adjustment invoice, and updates its pricing snapshot."
    ),
)
def change_subscription_plan(
    subscription_id: int,
    payload: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
) -> SubscriptionPlanChangeResponse:
    try:
        sub = SubscriptionService.change_plan(db, subscription_id, payload)
        resp = SubscriptionPlanChangeResponse.model_validate(sub)
        if hasattr(sub, "_last_invoice") and sub._last_invoice:
            resp.invoice_id = sub._last_invoice.id
            resp.invoice_number = sub._last_invoice.invoice_number
        if hasattr(sub, "_last_proration") and sub._last_proration:
            resp.proration_net_amount = sub._last_proration.net_amount_due
        return resp
    except (SubscriptionNotFoundError, PlanNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (PlanArchivedError, InvalidSubscriptionStateError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get(
    "/{subscription_id}/history",
    response_model=List[SubscriptionHistoryEvent],
    status_code=status.HTTP_200_OK,
    summary="Get Subscription History Timeline",
    description="Returns a chronological timeline of plan changes, proration adjustments, and lifecycle events for the subscription.",
)
def get_subscription_history(
    subscription_id: int,
    db: Session = Depends(get_db),
) -> List[SubscriptionHistoryEvent]:
    try:
        return SubscriptionService.get_subscription_history(db, subscription_id)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{subscription_id}/pause",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Pause Active Subscription",
    description=(
        "Pauses an active paid subscription using the orthogonal is_paused flag while preserving status='active', "
        "freezing unused prepaid seconds and halting automated billing."
    ),
)
def pause_subscription(
    subscription_id: int,
    payload: SubscriptionPauseRequest = Body(default_factory=SubscriptionPauseRequest),
    db: Session = Depends(get_db),
) -> SubscriptionResponse:
    try:
        sub = SubscriptionService.pause_subscription(db, subscription_id, payload)
        return SubscriptionResponse.model_validate(sub)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SubscriptionPauseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidSubscriptionStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{subscription_id}/resume",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Resume Paused Subscription",
    description=(
        "Resumes a paused active subscription, restoring remaining prepaid duration and shifting the renewal date."
    ),
)
def resume_subscription(
    subscription_id: int,
    payload: SubscriptionResumeRequest = Body(default_factory=SubscriptionResumeRequest),
    db: Session = Depends(get_db),
) -> SubscriptionResponse:
    try:
        sub = SubscriptionService.resume_subscription(db, subscription_id, payload)
        return SubscriptionResponse.model_validate(sub)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SubscriptionPauseConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidSubscriptionStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{subscription_id}/cancel",
    response_model=SubscriptionResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Subscription",
    description="Supports immediate cancellation (status -> cancelled) or end-of-billing-cycle cancellation (cancel_at_period_end=true).",
)
def cancel_subscription(
    subscription_id: int,
    payload: SubscriptionCancelRequest = Body(default_factory=SubscriptionCancelRequest),
    db: Session = Depends(get_db),
) -> SubscriptionResponse:
    try:
        sub = SubscriptionService.cancel_subscription(db, subscription_id, payload)
        return SubscriptionResponse.model_validate(sub)
    except SubscriptionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidSubscriptionStateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

