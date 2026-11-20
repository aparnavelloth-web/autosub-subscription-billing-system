import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.enums import BillingInterval
from app.schemas.common import PaginatedResponse
from app.schemas.plan import PlanCreate, PlanResponse, PlanUpdate
from app.services.plan_service import (
    DuplicatePlanCodeError,
    PlanArchivedError,
    PlanNotFoundError,
    PlanService,
)

router = APIRouter()


@router.post(
    "",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Subscription Plan",
    description="Creates a new recurring subscription plan with configurable pricing, interval, trial period, and feature entitlements.",
)
def create_plan(
    payload: PlanCreate,
    db: Session = Depends(get_db),
) -> PlanResponse:
    try:
        plan = PlanService.create_plan(db, payload)
        return PlanResponse.model_validate(plan)
    except DuplicatePlanCodeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "",
    response_model=PaginatedResponse[PlanResponse],
    status_code=status.HTTP_200_OK,
    summary="List Subscription Plans",
    description="Retrieves a paginated list of subscription plans. Archived plans are excluded by default.",
)
def list_plans(
    page: int = Query(default=1, ge=1, description="1-indexed page number"),
    size: int = Query(default=20, ge=1, le=100, description="Number of plans per page"),
    include_archived: bool = Query(default=False, description="Include soft-deleted / archived plans"),
    active_only: bool = Query(default=True, description="Filter to active plans only (when include_archived is false)"),
    billing_interval: Optional[BillingInterval] = Query(default=None, description="Filter by billing interval"),
    db: Session = Depends(get_db),
) -> PaginatedResponse[PlanResponse]:
    items, total = PlanService.list_plans(
        db=db,
        page=page,
        size=size,
        include_archived=include_archived,
        active_only=active_only,
        billing_interval=billing_interval,
    )
    pages = math.ceil(total / size) if total > 0 else 0
    return PaginatedResponse[PlanResponse](
        items=[PlanResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/{plan_id}",
    response_model=PlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Subscription Plan by ID",
)
def get_plan(
    plan_id: int,
    db: Session = Depends(get_db),
) -> PlanResponse:
    try:
        plan = PlanService.get_plan(db, plan_id)
        return PlanResponse.model_validate(plan)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put(
    "/{plan_id}",
    response_model=PlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Subscription Plan",
    description="Updates plan pricing, trial days, or entitlements. Existing subscriptions retain their historical pricing snapshot.",
)
def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    db: Session = Depends(get_db),
) -> PlanResponse:
    try:
        plan = PlanService.update_plan(db, plan_id, payload)
        return PlanResponse.model_validate(plan)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DuplicatePlanCodeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PlanArchivedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch(
    "/{plan_id}/archive",
    response_model=PlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Archive Subscription Plan",
    description="Soft-deletes a plan so it cannot be used for new subscriptions while preserving historical subscriptions.",
)
def archive_plan(
    plan_id: int,
    db: Session = Depends(get_db),
) -> PlanResponse:
    try:
        plan = PlanService.archive_plan(db, plan_id)
        return PlanResponse.model_validate(plan)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PlanArchivedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete(
    "/{plan_id}",
    response_model=PlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete Subscription Plan",
    description="Soft-deletes a plan so it cannot be used for new subscriptions while strictly preserving historical subscriptions, price snapshots, and financial records.",
)
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
) -> PlanResponse:
    try:
        plan = PlanService.delete_plan(db, plan_id)
        return PlanResponse.model_validate(plan)
    except PlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PlanArchivedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

