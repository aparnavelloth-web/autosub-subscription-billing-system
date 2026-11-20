from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog
from app.models.enums import BillingInterval
from app.models.plan import Plan
from app.schemas.plan import PlanCreate, PlanUpdate


class PlanNotFoundError(Exception):
    """Raised when a requested plan ID does not exist."""


class DuplicatePlanCodeError(Exception):
    """Raised when attempting to create or rename a plan with an existing code."""


class PlanArchivedError(Exception):
    """Raised when an operation is disallowed on an archived or inactive plan."""


def _serialize_plan(plan: Plan) -> Dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "code": plan.code,
        "description": plan.description,
        "price": str(plan.price),
        "currency": plan.currency,
        "billing_interval": plan.billing_interval.value if plan.billing_interval else None,
        "trial_days": plan.trial_days,
        "features": plan.features,
        "is_active": plan.is_active,
        "deleted_at": plan.deleted_at.isoformat() if plan.deleted_at else None,
    }


class PlanService:
    """
    Domain business service encapsulating subscription plan management,
    uniqueness guarantees, archival rules, and audit logging.
    """

    @staticmethod
    def create_plan(db: Session, payload: PlanCreate, actor: str = "api") -> Plan:
        existing = db.query(Plan).filter(Plan.code == payload.code).first()
        if existing is not None:
            raise DuplicatePlanCodeError(f"A plan with code '{payload.code}' already exists.")

        plan = Plan(**payload.model_dump())
        db.add(plan)
        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="plan",
                    entity_id=plan.id,
                    action="plan.created",
                    actor=actor,
                    old_values=None,
                    new_values=_serialize_plan(plan),
                    description=f"Created subscription plan '{plan.code}'.",
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicatePlanCodeError(f"A plan with code '{payload.code}' already exists.") from exc

        db.refresh(plan)
        return plan

    @staticmethod
    def list_plans(
        db: Session,
        page: int = 1,
        size: int = 20,
        include_archived: bool = False,
        active_only: bool = True,
        billing_interval: Optional[BillingInterval] = None,
    ) -> Tuple[List[Plan], int]:
        query = db.query(Plan)

        if not include_archived:
            query = query.filter(Plan.deleted_at.is_(None))
            if active_only:
                query = query.filter(Plan.is_active.is_(True))

        if billing_interval is not None:
            query = query.filter(Plan.billing_interval == billing_interval)

        total = query.count()
        items = (
            query.order_by(Plan.id.asc())
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )
        return items, total

    @staticmethod
    def get_plan(db: Session, plan_id: int) -> Plan:
        plan = db.get(Plan, plan_id)
        if plan is None:
            raise PlanNotFoundError(f"Plan with id {plan_id} not found.")
        return plan

    @staticmethod
    def ensure_plan_available_for_subscription(db: Session, plan_id: int) -> Plan:
        """
        Validates that a plan exists, is active, and has not been archived
        before allowing a new subscription to reference it.
        """
        plan = PlanService.get_plan(db, plan_id)
        if plan.is_deleted or not plan.is_active:
            raise PlanArchivedError(
                f"Plan '{plan.code}' (id={plan.id}) is archived or inactive and cannot be used for new subscriptions."
            )
        return plan

    @staticmethod
    def update_plan(db: Session, plan_id: int, payload: PlanUpdate, actor: str = "api") -> Plan:
        plan = PlanService.get_plan(db, plan_id)
        if plan.is_deleted:
            raise PlanArchivedError(f"Plan '{plan.code}' (id={plan.id}) is archived and cannot be updated.")

        update_data = payload.model_dump(exclude_unset=True)
        if "code" in update_data and update_data["code"] != plan.code:
            duplicate = db.query(Plan).filter(Plan.code == update_data["code"]).first()
            if duplicate is not None:
                raise DuplicatePlanCodeError(f"A plan with code '{update_data['code']}' already exists.")

        old_values = _serialize_plan(plan)
        for field, value in update_data.items():
            setattr(plan, field, value)

        try:
            db.flush()
            db.add(
                AuditLog(
                    entity_type="plan",
                    entity_id=plan.id,
                    action="plan.updated",
                    actor=actor,
                    old_values=old_values,
                    new_values=_serialize_plan(plan),
                    description=f"Updated subscription plan '{plan.code}'.",
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise DuplicatePlanCodeError("Plan code conflicts with an existing plan.") from exc

        db.refresh(plan)
        return plan

    @staticmethod
    def archive_plan(db: Session, plan_id: int, actor: str = "api") -> Plan:
        plan = PlanService.get_plan(db, plan_id)
        if plan.is_deleted:
            raise PlanArchivedError(f"Plan '{plan.code}' (id={plan.id}) is already archived.")

        old_values = _serialize_plan(plan)
        plan.soft_delete()
        db.flush()
        db.add(
            AuditLog(
                entity_type="plan",
                entity_id=plan.id,
                action="plan.archived",
                actor=actor,
                old_values=old_values,
                new_values=_serialize_plan(plan),
                description=f"Archived subscription plan '{plan.code}'.",
            )
        )
        db.commit()
        db.refresh(plan)
        return plan

    @staticmethod
    def delete_plan(db: Session, plan_id: int, actor: str = "api") -> Plan:
        """
        Soft-deletes / archives a subscription plan.
        Preserves existing historical subscriptions, invoices, and billing cycles,
        preventing foreign key violations while disallowing new subscriptions.
        """
        plan = PlanService.get_plan(db, plan_id)
        if plan.is_deleted:
            raise PlanArchivedError(f"Plan '{plan.code}' (id={plan.id}) is already archived or deleted.")

        old_values = _serialize_plan(plan)
        plan.soft_delete()
        db.flush()
        db.add(
            AuditLog(
                entity_type="plan",
                entity_id=plan.id,
                action="plan.deleted",
                actor=actor,
                old_values=old_values,
                new_values=_serialize_plan(plan),
                description=f"Soft-deleted subscription plan '{plan.code}'.",
            )
        )
        db.commit()
        db.refresh(plan)
        return plan

